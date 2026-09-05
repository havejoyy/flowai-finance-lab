#!/usr/bin/env python3
"""Fetch or read A-share daily bars and build an evidence-based stock character profile."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json,text/plain,*/*",
}


def secid(code: str) -> str:
    code = code.strip().zfill(6)
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


def parse_date(value: str) -> date:
    return datetime.strptime(value.strip()[:10].replace("/", "-"), "%Y-%m-%d").date()


def fetch_bars(code: str, end: str, limit: int = 180) -> tuple[str, list[dict[str, Any]]]:
    params = {
        "secid": secid(code), "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101", "fqt": "1", "beg": "0", "end": end.replace("-", ""), "lmt": str(limit),
    }
    request = urllib.request.Request(KLINE_URL + "?" + urllib.parse.urlencode(params), headers=HEADERS)
    last_error: Exception | None = None
    payload = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            break
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.8 * (attempt + 1))
    if payload is None:
        raise RuntimeError(f"行情接口连续5次失败: {last_error}")
    data = payload.get("data") or {}
    lines = data.get("klines") or []
    if not lines:
        raise RuntimeError("行情接口未返回日线，请检查代码、日期或网络")
    bars = []
    for line in lines:
        values = line.split(",")
        bars.append({
            "date": parse_date(values[0]), "open": float(values[1]), "close": float(values[2]),
            "high": float(values[3]), "low": float(values[4]), "volume": float(values[5]),
            "amount": float(values[6]), "turnover_rate": float(values[10]) if len(values) > 10 and values[10] not in ("", "-") else None,
        })
    return data.get("name") or code, bars


def load_bars(path: Path) -> tuple[str, list[dict[str, Any]]]:
    bars = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "open", "close", "high", "low", "volume", "amount"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("日线CSV缺少字段: " + ", ".join(sorted(required - set(reader.fieldnames or []))))
        for row in reader:
            bars.append({
                "date": parse_date(row["date"]), "open": float(row["open"]), "close": float(row["close"]),
                "high": float(row["high"]), "low": float(row["low"]), "volume": float(row["volume"]),
                "amount": float(row["amount"]), "turnover_rate": float(row["turnover_rate"]) if row.get("turnover_rate") else None,
            })
    bars.sort(key=lambda item: item["date"])
    return path.stem, bars


def pct_change(current: float, previous: float) -> float:
    return current / previous - 1 if previous else 0.0


def maximum_drawdown(closes: list[float]) -> float:
    peak = closes[0]
    worst = 0.0
    for value in closes:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def analyze(code: str, name: str, bars: list[dict[str, Any]], source: str) -> dict[str, Any]:
    if len(bars) < 61:
        raise ValueError(f"至少需要61根日线，当前只有{len(bars)}根")
    bars = bars[-120:]
    closes = [item["close"] for item in bars]
    returns = [pct_change(closes[i], closes[i - 1]) for i in range(1, len(closes))]
    recent20 = bars[-20:]
    recent60 = bars[-60:]
    returns20 = returns[-20:]
    vol20 = statistics.stdev(returns20) * math.sqrt(252) if len(returns20) > 1 else 0
    true_ranges = []
    for index in range(len(bars) - 14, len(bars)):
        previous = bars[index - 1]["close"]
        current = bars[index]
        true_ranges.append(max(current["high"] - current["low"], abs(current["high"] - previous), abs(current["low"] - previous)))
    atr_pct = statistics.mean(true_ranges) / closes[-1]
    amplitude20 = statistics.mean((item["high"] - item["low"]) / item["close"] for item in recent20)
    path20 = sum(abs(closes[i] - closes[i - 1]) for i in range(len(closes) - 19, len(closes)))
    efficiency20 = abs(closes[-1] - closes[-20]) / path20 if path20 else 0
    return20 = pct_change(closes[-1], closes[-20])
    large_move_rate = sum(abs(pct_change(recent60[i]["close"], recent60[i - 1]["close"])) >= 0.07 for i in range(1, 60)) / 59
    gap_rate = sum(abs(pct_change(recent60[i]["open"], recent60[i - 1]["close"])) >= 0.02 for i in range(1, 60)) / 59
    amount20 = [item["amount"] for item in recent20]
    amount_median = statistics.median(amount20)
    amount_cv = statistics.stdev(amount20) / statistics.mean(amount20) if statistics.mean(amount20) and len(amount20) > 1 else 0
    turnovers = [item["turnover_rate"] for item in recent20 if item["turnover_rate"] is not None]
    turnover_median = statistics.median(turnovers) if turnovers else None
    drawdown60 = maximum_drawdown([item["close"] for item in recent60])
    from_high60 = closes[-1] / max(item["high"] for item in recent60) - 1
    labels = []
    evidence = []
    if efficiency20 >= 0.45 and abs(return20) >= 0.08:
        label = "高波动趋势" if vol20 >= 0.45 else "稳定趋势"
        labels.append(label)
        evidence.append(f"{label}: 20日方向效率{efficiency20:.2f}、区间收益{return20:.1%}、年化波动{vol20:.1%}")
    if efficiency20 <= 0.25 and vol20 <= 0.25:
        labels.append("低波动震荡")
        evidence.append(f"低波动震荡: 方向效率{efficiency20:.2f}、年化波动{vol20:.1%}")
    if large_move_rate >= 0.10 and efficiency20 < 0.45:
        labels.append("高频脉冲")
        evidence.append(f"高频脉冲: 60日绝对涨跌≥7%的交易日占{large_move_rate:.1%}")
    if vol20 >= 0.45 or amplitude20 >= 0.05:
        labels.append("高弹性")
        evidence.append(f"高弹性: 年化波动{vol20:.1%}、20日平均振幅{amplitude20:.1%}")
    if amount_median < 100_000_000:
        labels.append("流动性偏弱")
        evidence.append(f"流动性偏弱: 20日成交额中位数{amount_median / 100_000_000:.2f}亿元")
    if not labels:
        labels = ["混合型/暂不定型"]
        evidence = ["没有默认标签同时满足全部条件，保留五维指标而不强行归类"]
    metrics = {
        "annualized_volatility_20d_pct": round(vol20 * 100, 2), "atr_14d_pct": round(atr_pct * 100, 2),
        "average_amplitude_20d_pct": round(amplitude20 * 100, 2), "return_20d_pct": round(return20 * 100, 2),
        "direction_efficiency_20d": round(efficiency20, 3), "median_amount_20d_cny": round(amount_median, 2),
        "amount_cv_20d": round(amount_cv, 3), "median_turnover_20d_pct": round(turnover_median, 2) if turnover_median is not None else None,
        "large_move_rate_60d_pct": round(large_move_rate * 100, 2), "gap_rate_60d_pct": round(gap_rate * 100, 2),
        "max_drawdown_60d_pct": round(drawdown60 * 100, 2), "distance_from_60d_high_pct": round(from_high60 * 100, 2),
    }
    return {
        "schema": "stock-character-analysis/v1", "security_code": code or None, "security_name": name,
        "as_of": bars[-1]["date"].isoformat(), "source": source, "adjustment": "前复权" if source.startswith("东方财富公开行情接口") else "由输入文件决定",
        "sample_size": len(bars), "labels": labels, "label_evidence": evidence, "metrics": metrics,
        "dimensions": {
            "volatility": {"evidence": f"20日年化波动{vol20:.1%}，ATR占比{atr_pct:.1%}，平均振幅{amplitude20:.1%}"},
            "trend": {"evidence": f"20日收益{return20:.1%}，方向效率{efficiency20:.2f}"},
            "liquidity": {"evidence": f"20日成交额中位数{amount_median / 100_000_000:.2f}亿元，稳定系数{amount_cv:.2f}"},
            "activity": {"evidence": f"60日大幅波动频率{large_move_rate:.1%}，跳空频率{gap_rate:.1%}"},
            "drawdown_recovery": {"evidence": f"60日最大回撤{drawdown60:.1%}，距60日高点{from_high60:.1%}"},
        },
        "limitations": ["标签依赖观察窗口，不代表永久属性", "成交额只是流动性代理，不等于盘口深度", "价格行为不能证明主力或游资意图"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="A股五维股性分析")
    parser.add_argument("code", nargs="?", help="六位A股代码")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"), help="截止日期 YYYY-MM-DD")
    parser.add_argument("--input", type=Path, help="本地日线CSV")
    parser.add_argument("--json", action="store_true", help="输出JSON")
    parser.add_argument("--output", type=Path, help="保存JSON")
    args = parser.parse_args()
    if not args.input and not args.code:
        parser.error("请提供股票代码或 --input 日线CSV")
    if args.input:
        name, bars = load_bars(args.input)
        result = analyze(args.code or "", name, bars, f"本地文件:{args.input}")
    else:
        name, bars = fetch_bars(args.code, args.end)
        result = analyze(args.code.zfill(6), name, bars, "东方财富公开行情接口")
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
