#!/usr/bin/env python3
"""获取 A 股每日市场快照，纯 Python 标准库实现。"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any


CLIST_URLS = [
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://20.push2.eastmoney.com/api/qt/clist/get",
]
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}
UNIVERSE = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
FIELDS = "f2,f3,f5,f6,f8,f12,f14,f15,f16,f17,f18,f20,f21,f100,f102"
INDICES = {"sh000001": ("1.000001", "上证指数"), "sz399001": ("0.399001", "深证成指"), "sz399006": ("0.399006", "创业板指")}


def api_get(url: str, params: dict[str, str], retries: int = 4) -> dict[str, Any]:
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(full_url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("rc") not in (None, 0) or payload.get("data") is None:
                raise RuntimeError(f"API 返回失败: rc={payload.get('rc')}")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    assert last_error is not None
    raise last_error


def number(value: Any) -> float | int | None:
    if value in (None, "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_universe() -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        params = {"pn": str(page), "pz": "500", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f3", "fs": UNIVERSE, "fields": FIELDS}
        failures = []
        payload = None
        for url in CLIST_URLS:
            try:
                payload = api_get(url, params, retries=1)
                break
            except Exception as exc:
                failures.append(f"{url}: {exc}")
        if payload is None:
            raise RuntimeError("全市场快照节点均失败: " + " | ".join(failures))
        data = payload["data"]
        current = data.get("diff") or []
        rows.extend(current)
        if len(rows) >= int(data.get("total") or 0) or not current:
            break
        page += 1
        time.sleep(0.15)
    return rows


def secid(code: str) -> str:
    return f"{1 if code.startswith(('6', '9')) else 0}.{code}"


def fetch_klines(sec_id: str, begin: str, end: str) -> tuple[str | None, list[list[str]]]:
    payload = api_get(KLINE_URL, {"secid": sec_id, "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61", "klt": "101", "fqt": "1", "beg": begin, "end": end})
    data = payload["data"]
    return data.get("name"), [line.split(",") for line in (data.get("klines") or [])]


def limit_pct(code: str, name: str) -> float:
    if "ST" in name.upper():
        return 5.0
    if code.startswith(("30", "68")):
        return 20.0
    if code.startswith(("4", "8")):
        return 30.0
    return 10.0


def is_at_limit(change: float | None, threshold: float, direction: int = 1) -> bool:
    return change is not None and direction * change >= threshold - 0.2


def stock_record(row: dict) -> dict:
    return {
        "code": str(row.get("f12") or ""), "name": row.get("f14") or "",
        "close": number(row.get("f2")), "change_rate": number(row.get("f3")),
        "volume_shares": number(row.get("f5")), "amount_yuan": number(row.get("f6")),
        "turnover_rate": number(row.get("f8")), "high": number(row.get("f15")),
        "low": number(row.get("f16")), "open": number(row.get("f17")),
        "previous_close": number(row.get("f18")), "market_cap_yuan": number(row.get("f20")),
        "free_market_cap_yuan": number(row.get("f21")), "industry": row.get("f100") or None,
        "region": row.get("f102") or None,
    }


def enrich_candidate(stock: dict, date: str) -> dict:
    result = dict(stock)
    try:
        _, lines = fetch_klines(secid(stock["code"]), "19900101", date.replace("-", ""))
        recent = lines[-10:]
        closes = [float(x[2]) for x in recent]
        amounts = [float(x[6]) for x in recent]
        changes = [float(x[8]) for x in recent]
        streak = 0
        threshold = limit_pct(stock["code"], stock["name"])
        for change in reversed(changes):
            if is_at_limit(change, threshold): streak += 1
            else: break
        previous = amounts[-6:-1]
        result["board_height"] = streak
        result["volume_ratio_5d"] = round(amounts[-1] / (sum(previous) / len(previous)), 2) if len(previous) == 5 and sum(previous) else None
        result["breakout_previous_5d_high"] = len(closes) >= 6 and closes[-1] > max(closes[-6:-1])
    except Exception as exc:
        result["history_error"] = str(exc)
        result["board_height"] = None
        result["volume_ratio_5d"] = None
        result["breakout_previous_5d_high"] = None
    return result


def build_payload(date: str, rows: list[dict], indices: dict[str, dict]) -> dict:
    stocks = [stock_record(row) for row in rows]
    valid = [s for s in stocks if s["change_rate"] is not None]
    limit_ups, limit_downs, broken = [], [], []
    for stock in valid:
        threshold = limit_pct(stock["code"], stock["name"])
        if is_at_limit(stock["change_rate"], threshold): limit_ups.append(stock)
        if is_at_limit(stock["change_rate"], threshold, -1): limit_downs.append(stock)
        prev, high = stock["previous_close"], stock["high"]
        high_change = ((high / prev) - 1) * 100 if high and prev else None
        if is_at_limit(high_change, threshold) and not is_at_limit(stock["change_rate"], threshold): broken.append(stock)

    history_available = any(item.get("close") is not None for item in indices.values())
    if history_available:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(enrich_candidate, stock, date): stock["code"] for stock in limit_ups}
            enriched = [future.result() for future in as_completed(futures)]
    else:
        enriched = [{**stock, "board_height": None, "volume_ratio_5d": None, "breakout_previous_5d_high": None, "history_error": "指数历史节点不可用，已熔断个股历史增强"} for stock in limit_ups]
    enriched.sort(key=lambda s: (s.get("board_height") or 0, s.get("amount_yuan") or 0), reverse=True)

    sectors: dict[str, dict] = {}
    for stock in valid:
        name = stock.get("industry") or "行业未知"
        item = sectors.setdefault(name, {"name": name, "stock_count": 0, "up_count": 0, "limit_up_count": 0, "amount_yuan": 0.0, "changes": [], "representative_stocks": []})
        item["stock_count"] += 1
        item["up_count"] += stock["change_rate"] > 0
        item["amount_yuan"] += stock.get("amount_yuan") or 0
        item["changes"].append(stock["change_rate"])
    enriched_by_code = {s["code"]: s for s in enriched}
    for stock in limit_ups:
        item = sectors[stock.get("industry") or "行业未知"]
        item["limit_up_count"] += 1
        item["representative_stocks"].append(enriched_by_code.get(stock["code"], stock))
    sector_rows = []
    for item in sectors.values():
        item["average_change_rate"] = round(sum(item.pop("changes")) / item["stock_count"], 2)
        item["representative_stocks"] = item["representative_stocks"][:5]
        item["classification"] = "主线候选" if item["limit_up_count"] >= 3 and item["average_change_rate"] > 0 else "轮动/一般"
        sector_rows.append(item)
    sector_rows.sort(key=lambda x: (x["limit_up_count"], x["average_change_rate"], x["amount_yuan"]), reverse=True)

    touched = len(limit_ups) + len(broken)
    errors = []
    if not valid: errors.append({"scope": "market", "error": "无有效全市场行情"})
    return {
        "schema_version": "daily-market-review/v1", "trade_date": date,
        "source": "东方财富公开行情接口", "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete": not errors, "errors": errors,
        "market": {
            "indices": indices,
            "stock_universe_count": len(valid), "up_count": sum(s["change_rate"] > 0 for s in valid),
            "down_count": sum(s["change_rate"] < 0 for s in valid), "flat_count": sum(s["change_rate"] == 0 for s in valid),
            "amount_yuan": sum((s.get("amount_yuan") or 0) for s in valid),
        },
        "sentiment": {
            "limit_up_count": len(limit_ups), "limit_down_count": len(limit_downs),
            "broken_limit_count": len(broken), "touched_limit_up_count": touched,
            "broken_board_rate": round(len(broken) / touched, 4) if touched else None,
            "max_board_height": max((s.get("board_height") for s in enriched if s.get("board_height") is not None), default=None),
        },
        "boards": {"type": "industry", "items": sector_rows},
        "stocks": valid,
        "representative_stocks": enriched[:30],
        "limit_down_stocks": limit_downs[:30], "broken_limit_stocks": broken[:30],
        "limitations": ["主线候选按行业涨停数量与行业平均涨幅生成，不包含新闻驱动", "炸板由日内最高价触及涨停且收盘未封板推算", "代表性标的不等同于经人工确认的市场龙头"],
    }


def fetch_indices(date: str) -> dict[str, dict]:
    result = {}
    compact = date.replace("-", "")
    for key, (sid, label) in INDICES.items():
        try:
            _, lines = fetch_klines(sid, compact, compact)
        except Exception as exc:
            result[key] = {"name": label, "status": "missing", "error": str(exc)}
            continue
        if not lines:
            result[key] = {"name": label, "status": "missing"}
            continue
        row = lines[-1]
        result[key] = {"name": label, "close": float(row[2]), "change_rate": float(row[8]), "amount_yuan": float(row[6])}
    return result


def fetch_latest_trade_date() -> str | None:
    """确认实时全市场快照对应的最近交易日，避免与历史日期错配。"""
    payload = api_get(KLINE_URL, {"secid": "1.000001", "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61", "klt": "101", "fqt": "1", "beg": "0", "end": "20500101", "lmt": "1"})
    lines = payload["data"].get("klines") or []
    return lines[-1].split(",", 1)[0] if lines else None


def main() -> int:
    parser = argparse.ArgumentParser(description="获取 A 股每日市场复盘标准快照")
    parser.add_argument("date", help="交易日期 YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="输出标准 JSON（默认亦为 JSON，保留兼容开关）")
    args = parser.parse_args()
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
        indices = fetch_indices(args.date)
        latest_date = None
        try:
            latest_date = fetch_latest_trade_date()
        except Exception:
            pass
        if latest_date and latest_date != args.date:
            raise RuntimeError(f"全市场接口仅提供最近交易日快照（{latest_date}），不能与历史日期 {args.date} 混用")
        if not latest_date and args.date != datetime.now().astimezone().date().isoformat():
            raise RuntimeError("交易日校验接口不可用，拒绝将实时全市场快照用于非当日日期")
        payload = build_payload(args.date, fetch_universe(), indices)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"schema_version": "daily-market-review/v1", "trade_date": args.date, "complete": False, "errors": [{"scope": "fetch", "error": str(exc)}]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
