#!/usr/bin/env python3
"""获取东方财富龙虎榜当日数据，输出 Markdown 供 longhubang-analysis Skill 解读。

纯标准库实现，无需安装第三方包。

用法:
    python3 fetch_longhubang.py 2026-08-07
    python3 fetch_longhubang.py 2026-08-07 > lhb-2026-08-07.md
    python3 fetch_longhubang.py 2026-08-07 --json > lhb-2026-08-07.json

数据来源: 东方财富数据中心公开 API (RPT_DAILYBILLBOARD_DETAILSNEW)
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime


API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_DAILYBILLBOARD_DETAILSNEW"
COLUMNS = ",".join([
    "SECURITY_CODE", "SECUCODE", "SECURITY_NAME_ABBR", "TRADE_DATE",
    "EXPLAIN", "CLOSE_PRICE", "CHANGE_RATE", "TURNOVERRATE",
    "BILLBOARD_NET_AMT", "BILLBOARD_BUY_AMT", "BILLBOARD_SELL_AMT",
    "BILLBOARD_DEAL_AMT", "ACCUM_AMOUNT", "DEAL_NET_RATIO",
    "DEAL_AMOUNT_RATIO", "FREE_MARKET_CAP", "EXPLANATION",
    "D1_CLOSE_ADJCHRATE", "D2_CLOSE_ADJCHRATE", "D5_CLOSE_ADJCHRATE",
    "D10_CLOSE_ADJCHRATE", "MARKET", "TRADE_ID", "TRADE_MARKET",
    "SECURITY_TYPE_CODE",
])
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/stock/tradedetail.html",
}


def fetch_page(date: str, page: int = 1, page_size: int = 200) -> list[dict]:
    """获取单页龙虎榜数据。"""
    flt = f"(TRADE_DATE<='{date}')(TRADE_DATE>='{date}')"
    params = {
        "reportName": REPORT_NAME,
        "columns": COLUMNS,
        "source": "WEB",
        "client": "WEB",
        "filter": flt,
        "sortColumns": "SECURITY_CODE",
        "sortTypes": "1",
        "pageSize": str(page_size),
        "pageNumber": str(page),
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8")
    payload = json.loads(body)
    if not payload.get("success"):
        msg = payload.get("message", "unknown error")
        raise RuntimeError(f"API 返回失败: {msg}")
    result = payload.get("result") or {}
    return result.get("data") or []


def fetch_all(date: str) -> list[dict]:
    """获取指定日期全部龙虎榜数据（自动翻页）。"""
    all_rows: list[dict] = []
    page = 1
    while True:
        rows = fetch_page(date, page=page, page_size=200)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < 200:
            break
        page += 1
    return all_rows


def dedupe_stocks(rows: list[dict]) -> list[dict]:
    """按股票代码去重，合并上榜原因和席位摘要。"""
    stocks: dict[str, dict] = {}
    for r in rows:
        code = r["SECURITY_CODE"]
        if code not in stocks:
            stocks[code] = {
                "code": code,
                "secucode": r.get("SECUCODE", ""),
                "name": r.get("SECURITY_NAME_ABBR", ""),
                "market": r.get("MARKET", ""),
                "trade_market": r.get("TRADE_MARKET", ""),
                "close": r.get("CLOSE_PRICE", 0),
                "change_rate": r.get("CHANGE_RATE", 0),
                "turnover": r.get("TURNOVERRATE", 0),
                "net_amt": r.get("BILLBOARD_NET_AMT", 0),
                "buy_amt": r.get("BILLBOARD_BUY_AMT", 0),
                "sell_amt": r.get("BILLBOARD_SELL_AMT", 0),
                "deal_amt": r.get("BILLBOARD_DEAL_AMT", 0),
                "accum_amount": r.get("ACCUM_AMOUNT", 0),
                "free_cap": r.get("FREE_MARKET_CAP", 0),
                "reasons": set(),
                "explains": set(),
                "d1": r.get("D1_CLOSE_ADJCHRATE"),
                "d2": r.get("D2_CLOSE_ADJCHRATE"),
                "d5": r.get("D5_CLOSE_ADJCHRATE"),
                "d10": r.get("D10_CLOSE_ADJCHRATE"),
            }
        reason = r.get("EXPLANATION") or ""
        explain = r.get("EXPLAIN") or ""
        if reason:
            stocks[code]["reasons"].add(reason)
        if explain:
            stocks[code]["explains"].add(explain)
    return list(stocks.values())


def fmt_wan(yuan: float | None) -> str:
    """元 → 万元，带正负号。"""
    if yuan is None:
        return "—"
    wan = yuan / 1e4
    return f"{wan:+.0f}"


def fmt_pct(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{val:+.2f}%"


def to_markdown(date: str, stocks: list[dict]) -> str:
    """生成 Markdown 格式，可直接喂给 longhubang-analysis Skill。"""
    lines: list[str] = []
    lines.append(f"# 龙虎榜数据 · {date}")
    lines.append("")
    lines.append(f"> 数据来源: 东方财富数据中心 (RPT_DAILYBILLBOARD_DETAILSNEW)")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 上榜股票数: {len(stocks)} 只")
    lines.append("")

    # 涨跌分布
    up = [s for s in stocks if s["change_rate"] > 0]
    down = [s for s in stocks if s["change_rate"] < 0]
    limit_up = [s for s in stocks if s["change_rate"] >= 9.9]
    limit_down = [s for s in stocks if s["change_rate"] <= -9.9]
    lines.append("## 涨跌分布")
    lines.append("")
    lines.append(
        f"上涨 {len(up)} 只 / 下跌 {len(down)} 只 / "
        f"涨停 {len(limit_up)} 只 / 跌停 {len(limit_down)} 只"
    )
    lines.append("")

    # 净买入 TOP20
    lines.append("## 净买入 TOP 20")
    lines.append("")
    lines.append("| 代码 | 名称 | 市场 | 涨跌幅 | 换手率 | 净额(万) | 买入(万) | 卖出(万) | 席位摘要 | 上榜原因 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|---|")
    for s in sorted(stocks, key=lambda x: x["net_amt"], reverse=True)[:20]:
        reasons = " | ".join(sorted(s["reasons"])) if s["reasons"] else "—"
        explains = " | ".join(sorted(s["explains"])) if s["explains"] else "—"
        lines.append(
            f"| {s['code']} | {s['name']} | {s['market']} | "
            f"{fmt_pct(s['change_rate'])} | {s['turnover']:.1f}% | "
            f"{fmt_wan(s['net_amt'])} | {fmt_wan(s['buy_amt'])} | "
            f"{fmt_wan(s['sell_amt'])} | {explains} | {reasons} |"
        )
    lines.append("")

    # 净卖出 TOP20
    lines.append("## 净卖出 TOP 20")
    lines.append("")
    lines.append("| 代码 | 名称 | 市场 | 涨跌幅 | 换手率 | 净额(万) | 买入(万) | 卖出(万) | 席位摘要 | 上榜原因 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|---|")
    for s in sorted(stocks, key=lambda x: x["net_amt"])[:20]:
        reasons = " | ".join(sorted(s["reasons"])) if s["reasons"] else "—"
        explains = " | ".join(sorted(s["explains"])) if s["explains"] else "—"
        lines.append(
            f"| {s['code']} | {s['name']} | {s['market']} | "
            f"{fmt_pct(s['change_rate'])} | {s['turnover']:.1f}% | "
            f"{fmt_wan(s['net_amt'])} | {fmt_wan(s['buy_amt'])} | "
            f"{fmt_wan(s['sell_amt'])} | {explains} | {reasons} |"
        )
    lines.append("")

    # 全部个股明细
    lines.append("## 全部个股明细")
    lines.append("")
    lines.append("| 代码 | 名称 | 市场 | 收盘价 | 涨跌幅 | 换手率 | 净额(万) | 买入(万) | 卖出(万) | 成交额(万) | 席位摘要 | 上榜原因 | D1 | D5 |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---:|")
    for s in sorted(stocks, key=lambda x: x["net_amt"], reverse=True):
        reasons = " | ".join(sorted(s["reasons"])) if s["reasons"] else "—"
        explains = " | ".join(sorted(s["explains"])) if s["explains"] else "—"
        d1 = fmt_pct(s["d1"]) if s["d1"] is not None else "—"
        d5 = fmt_pct(s["d5"]) if s["d5"] is not None else "—"
        lines.append(
            f"| {s['code']} | {s['name']} | {s['market']} | "
            f"{s['close']} | {fmt_pct(s['change_rate'])} | {s['turnover']:.1f}% | "
            f"{fmt_wan(s['net_amt'])} | {fmt_wan(s['buy_amt'])} | "
            f"{fmt_wan(s['sell_amt'])} | {fmt_wan(s['deal_amt'])} | "
            f"{explains} | {reasons} | {d1} | {d5} |"
        )
    lines.append("")

    # 数据缺口提示
    lines.append("## 数据缺口")
    lines.append("")
    lines.append("- 席位明细（买入/卖出前五席位名称与金额）未包含，仅有东方财富 EXPLAIN 摘要字段")
    lines.append("- 如需完整席位明细，可从东方财富龙虎榜详情页手动获取，或使用 akshare 库")
    lines.append("- D1/D5 等后续涨跌幅字段为新上榜日数据，历史数据可能为空")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 以上数据由 fetch_longhubang.py 自动获取，仅供 longhubang-analysis Skill 解读使用。")
    lines.append("> 龙虎榜为滞后数据，不构成投资建议。市场有风险，投资需谨慎。")

    return "\n".join(lines)


def to_json(date: str, stocks: list[dict]) -> str:
    """生成 JSON 格式。"""
    for s in stocks:
        s["reasons"] = sorted(s["reasons"])
        s["explains"] = sorted(s["explains"])
    return json.dumps(
        {"date": date, "count": len(stocks), "stocks": stocks},
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="获取东方财富龙虎榜当日数据，输出 Markdown 或 JSON 供 Skill 解读。"
    )
    parser.add_argument("date", help="交易日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式（默认 Markdown）",
    )
    args = parser.parse_args()

    date = args.date.strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        print(f"错误: 日期格式应为 YYYY-MM-DD，收到 {date!r}", file=sys.stderr)
        return 1

    print(f"正在获取 {date} 龙虎榜数据...", file=sys.stderr)
    try:
        rows = fetch_all(date)
    except Exception as exc:
        print(f"获取数据失败: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print(f"警告: {date} 无龙虎榜数据（可能非交易日）", file=sys.stderr)
        return 1

    stocks = dedupe_stocks(rows)
    print(f"共获取 {len(rows)} 条记录，去重后 {len(stocks)} 只股票", file=sys.stderr)

    if args.json:
        print(to_json(date, stocks))
    else:
        print(to_markdown(date, stocks))

    return 0


if __name__ == "__main__":
    sys.exit(main())
