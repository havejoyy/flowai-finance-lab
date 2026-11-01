"""Portable, append-only forward observation workflow. Python 3.9+, stdlib only."""
from contextlib import contextmanager,closing
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

VERSION = "0.5.1-desktop"
CN = timezone(timedelta(hours=8))


def today():
    return datetime.now(CN).date().isoformat()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def day(value):
    return date.fromisoformat(str(value)).isoformat()


def code(value):
    value = str(value).strip().upper()
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value):
        raise ValueError("代码须为六位数字加交易所，例如 000001.SZ")
    return value


def positive(value):
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("价格和复权因子须为有限正数")
    return result


def csv_rows(content):
    rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    if len(rows) > 200000:
        raise ValueError("单次最多导入 200000 行")
    return rows


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workflow.sqlite3"
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, name TEXT, version TEXT,
                    body TEXT NOT NULL, UNIQUE(name,version));
                CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE,
                    plan_id TEXT, created_at TEXT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY, created_at TEXT,
                    source TEXT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prices(source TEXT, code TEXT, day TEXT,
                    close REAL, factor REAL, PRIMARY KEY(source,code,day));
                CREATE TABLE IF NOT EXISTS calendar(source TEXT, day TEXT, is_open INTEGER,
                    PRIMARY KEY(source,day));
                CREATE TABLE IF NOT EXISTS evaluations(id TEXT PRIMARY KEY, batch_id TEXT,
                    created_at TEXT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, status TEXT, started_at TEXT,
                    finished_at TEXT, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_reports(id TEXT PRIMARY KEY, evaluation_id TEXT,
                    created_at TEXT, model TEXT, body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS freeze_plan BEFORE UPDATE ON plans
                    BEGIN SELECT RAISE(ABORT,'plan is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS freeze_batch BEFORE UPDATE ON batches
                    BEGIN SELECT RAISE(ABORT,'batch is immutable'); END;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET status='INTERRUPTED', finished_at=? WHERE status='RUNNING'", (timestamp(),))

    def create_batch(self, payload):
        plan = payload["plan"]
        name, version = str(plan["name"]).strip(), str(plan["version"]).strip()
        if not name or not version or not str(plan.get("definition", "")).strip():
            raise ValueError("请填写规则名称、版本和条件说明")
        if any(isinstance(x, bool) or float(x) != int(x) for x in plan["horizons"]):
            raise ValueError("观察周期必须是整数")
        horizons = sorted(set(int(x) for x in plan["horizons"]))
        if not horizons or len(horizons) > 8 or any(x < 1 or x > 250 for x in horizons):
            raise ValueError("观察周期为 1–250 个交易日，最多 8 个周期")
        source = plan.get("source", "csv")
        if source not in ("csv", "tushare", "demo"):
            raise ValueError("数据源必须为 csv、tushare 或 demo")
        plan = {"name": name, "version": version, "definition": str(plan["definition"]),
                "horizons": horizons, "benchmark": code(plan["benchmark"]), "source": source,
                "price_basis": "close_times_adjustment_factor",
                "measurement": "signal_close_to_T_plus_N_close_not_trade_return"}
        as_of = day(payload["as_of"])
        if as_of > today():
            raise ValueError("不能创建未来日期的筛选批次")
        candidates = []
        for item in payload["candidates"]:
            candidates.append({"code": code(item["code"]), "reason": str(item.get("reason", "")),
                               "snapshot": item.get("snapshot", {})})
        if len(candidates) > 100 or len({x["code"] for x in candidates}) != len(candidates):
            raise ValueError("单批最多 100 个候选，代码不可重复")
        candidates.sort(key=lambda x: x["code"])
        if any(c['code'] == plan['benchmark'] for c in candidates):
            raise ValueError('基准指数不能同时作为股票候选')
        body = {"plan": plan, "as_of": as_of, "candidates": candidates,
                "origin": str(payload.get("origin", "manual")),
                "imported_later": as_of < today(),
                "note": str(payload.get("note", ""))}
        kind = payload.get("data_kind", "synthetic" if source == "demo" else "unknown")
        if kind not in ("synthetic", "real", "unknown"):
            raise ValueError("资料类型无效")
        body["data_kind"] = kind
        fingerprint, plan_id = digest(body), digest(plan)
        batch_id = str(uuid.uuid4())
        with self.connect() as db:
            existing = db.execute("SELECT id,body FROM plans WHERE name=? AND version=?", (name, version)).fetchone()
            if existing and existing["body"] != encode(plan):
                raise ValueError("同名同版本规则已有不同配置，请创建新版本（包括数据源/周期变更）")
            db.execute("INSERT OR IGNORE INTO plans VALUES(?,?,?,?)", (plan_id, name, version, encode(plan)))
            same = db.execute("SELECT id FROM batches WHERE fingerprint=?", (fingerprint,)).fetchone()
            if same:
                return {"id": same["id"], "deduplicated": True}
            db.execute("INSERT INTO batches VALUES(?,?,?,?,?)", (batch_id, fingerprint, plan_id, timestamp(), encode(body)))
        return {"id": batch_id, "deduplicated": False}

    def import_market(self, source, rows, calendar):
        if source not in ("csv", "tushare", "demo"):
            raise ValueError("未知数据源")
        cleaned, days = [], []
        keys, date_keys = set(), set()
        for row in rows:
            item = (source, code(row["code"]), day(row["date"]), positive(row["close"]), positive(row["factor"]))
            if item[2] > today() or item[1:3] in keys:
                raise ValueError("行情日期不能在未来，同次导入不可重复")
            keys.add(item[1:3]); cleaned.append(item)
        for row in calendar:
            d, opened = day(row["date"]), int(row["is_open"])
            if opened not in (0, 1) or d in date_keys:
                raise ValueError("日历重复或 is_open 非 0/1")
            date_keys.add(d); days.append((source, d, opened))
        with self.connect() as db:
            db.execute("INSERT INTO imports(created_at,source,body) VALUES(?,?,?)",
                       (timestamp(), source, encode({"prices": cleaned, "calendar": days})))
            db.executemany("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?)", cleaned)
            db.executemany("INSERT OR REPLACE INTO calendar VALUES(?,?,?)", days)
        return {"prices": len(cleaned), "calendar": len(days)}

    def batches(self):
        with self.connect() as db:
            return [{"id": r["id"], "created_at": r["created_at"], **json.loads(r["body"])}
                    for r in db.execute("SELECT * FROM batches ORDER BY created_at DESC")]

    def evaluate(self, batch_id, observed_through=None):
        observed = day(observed_through or today())
        if observed > today():
            raise ValueError("不能使用未来观察日期")
        with self.connect() as db:
            batch = db.execute("SELECT body FROM batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                raise ValueError("批次不存在")
            batch = json.loads(batch["body"])
            plan, start = batch["plan"], batch["as_of"]
            if observed < start:
                raise ValueError("观察日期早于批次日期")
            cal = {r["day"]: r["is_open"] for r in db.execute(
                "SELECT * FROM calendar WHERE source=? AND day BETWEEN ? AND ?", (plan["source"], start, observed))}
            expected_days = [(date.fromisoformat(start) + timedelta(days=i)).isoformat()
                             for i in range((date.fromisoformat(observed) - date.fromisoformat(start)).days + 1)]
            calendar_valid = all(d in cal for d in expected_days) and cal.get(start) == 1
            sessions = sorted(d for d, is_open in cal.items() if is_open and d >= start)
            prices = {(r["code"], r["day"]): {"close": r["close"], "factor": r["factor"]}
                      for r in db.execute("SELECT * FROM prices WHERE source=? AND day BETWEEN ? AND ?",
                                          (plan["source"], start, observed))}
            results = []
            for candidate in batch["candidates"]:
                for horizon in plan["horizons"]:
                    item = {"code": candidate["code"], "horizon": horizon, "status": "IMMATURE"}
                    if not calendar_valid:
                        item.update(status="MISSING_CALENDAR", reason="需完整覆盖筛选日至观察日的交易日历，筛选日须为交易日")
                    elif len(sessions) <= horizon:
                        item["reason"] = "等待后续交易日，未到期"
                    else:
                        window = sessions[:horizon + 1]
                        missing = [{"code": c, "date": d} for c in (candidate["code"], plan["benchmark"])
                                   for d in window if (c, d) not in prices]
                        item["target_date"] = window[-1]
                        if missing:
                            item.update(status="MISSING_PRICE", missing=missing,
                                        reason="缺行情或停牌未明确处理；不顺延、不填零")
                        else:
                            paths = {}
                            for c in (candidate["code"], plan["benchmark"]):
                                paths[c] = [{"date": d, **prices[(c, d)]} for d in window]
                            values = [x["close"] * x["factor"] for x in paths[candidate["code"]]]
                            base = [x["close"] * x["factor"] for x in paths[plan["benchmark"]]]
                            ret, bench = values[-1] / values[0] - 1, base[-1] / base[0] - 1
                            peak, drawdown = values[0], 0.0
                            for value in values:
                                peak = max(peak, value); drawdown = min(drawdown, value / peak - 1)
                            item.update(status="MATURE", observation_return=ret, benchmark_return=bench,
                                        excess_return=ret - bench, max_close_drawdown=drawdown, evidence=paths)
                    results.append(item)
            groups = []
            for horizon in plan["horizons"]:
                items = [x for x in results if x["horizon"] == horizon]
                mature = [x for x in items if x["status"] == "MATURE"]
                groups.append({"horizon": horizon, "candidates": len(items), "mature": len(mature),
                               "pending_or_missing": len(items) - len(mature),
                               "mean_observation_return": sum(x["observation_return"] for x in mature) / len(mature) if mature else None,
                               "mean_excess_return": sum(x["excess_return"] for x in mature) / len(mature) if mature else None})
            report = {"id": str(uuid.uuid4()), "batch_id": batch_id, "engine_version": VERSION,
                      "observed_through": observed, "batch": batch, "results": results, "summary": groups,
                      "limitations": ["候选收盘价表现观察，不是实盘收益或可成交回测，未计费用及滑点",
                                      "同批候选及不同观察周期不属于独立实验，不能据此宣称规则有效",
                                      "补录批次不证明当时可获得同样的信息", "行情修订会产生新评估，旧评估保留"]}
            db.execute("INSERT INTO evaluations VALUES(?,?,?,?)", (report["id"], batch_id, timestamp(), encode(report)))
        return report

    def state(self):
        with self.connect() as db:
            evaluations = [json.loads(r["body"]) for r in db.execute("SELECT body FROM evaluations ORDER BY rowid DESC LIMIT 100")]
            runs = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 20")]
            ai = [dict(r) for r in db.execute("SELECT * FROM ai_reports ORDER BY created_at DESC LIMIT 20")]
        return {"version": VERSION, "today": today(), "data_directory": str(self.root),
                "batches": self.batches(), "evaluations": evaluations, "runs": runs, "ai_reports": ai}

    def run(self, observed_through=None, sync=None):
        observed = day(observed_through or today())
        if observed > today():
            raise ValueError("不能运行未来日期")
        run_id, output = str(uuid.uuid4()), {"steps": [], "evaluations": []}
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, "RUNNING", timestamp(), None, encode(output)))
        try:
            batches = self.batches()
            if sync:
                try:
                    output["steps"].append({"step": "sync", "status": "COMPLETED", "result": sync(batches, observed)})
                except Exception as exc:
                    output["steps"].append({"step": "sync", "status": "FAILED", "error": str(exc)})
            for batch in batches:
                if batch["as_of"] > observed or batch['plan']['source'] == 'demo':
                    continue
                try:
                    result = self.evaluate(batch["id"], observed)
                    output["evaluations"].append(result["id"])
                    output["steps"].append({"batch": batch["id"], "status": "COMPLETED",
                        "waiting": sum(x["status"] == "IMMATURE" for x in result["results"]),
                        "missing": sum(x["status"].startswith("MISSING") for x in result["results"])})
                except Exception as exc:
                    output["steps"].append({"batch": batch["id"], "status": "FAILED", "error": str(exc)})
            partial = any(x["status"] == "FAILED" or x.get("missing") for x in output["steps"])
            status = "PARTIAL" if partial else "COMPLETED"
        except Exception as exc:
            output["error"], status = str(exc), "FAILED"
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?,finished_at=?,body=? WHERE id=?", (status, timestamp(), encode(output), run_id))
        return {"id": run_id, "status": status, **output}

    def backup(self):
        folder = self.root / "backups"
        folder.mkdir(exist_ok=True)
        path = folder / ("workflow-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6] + ".sqlite3")
        with self.connect() as db, closing(sqlite3.connect(path)) as target:
            db.backup(target)
        return str(path)

    def demo(self):
        payload = {"plan": {"name": "演示规则（虚构数据）", "version": "v1", "definition": "虚构候选，用于安装验收",
                   "horizons": [2, 5], "benchmark": "000300.SH", "source": "demo"},
                   "as_of": "2026-01-05", "origin": "synthetic-demo",
                   "candidates": [{"code": "000001.SZ", "reason": "虚构示例"}, {"code": "600000.SH", "reason": "虚构示例"}]}
        batch = self.create_batch(payload)
        rows, calendar, k = [], [], 0
        for i in range(8):
            d = date(2026, 1, 5) + timedelta(days=i)
            opened = int(d.weekday() < 5)
            calendar.append({"date": d.isoformat(), "is_open": opened})
            if opened:
                for c, value in (("000001.SZ", 10 + k), ("600000.SH", 20 - k), ("000300.SH", 100 + k)):
                    rows.append({"code": c, "date": d.isoformat(), "close": value, "factor": 1})
                k += 1
        self.import_market("demo", rows, calendar)
        return self.evaluate(batch["id"], "2026-01-12")
