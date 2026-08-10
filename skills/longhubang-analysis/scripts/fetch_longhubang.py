#!/usr/bin/env python3
"""获取东方财富龙虎榜汇总及买卖前五席位，纯 Python 标准库实现。"""

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


API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SUMMARY_REPORT = "RPT_DAILYBILLBOARD_DETAILSNEW"
DETAIL_REPORTS = {
    "buy": ("RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
    "sell": ("RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
}
SUMMARY_COLUMNS = ",".join(
    [
        "SECURITY_CODE", "SECUCODE", "SECURITY_NAME_ABBR", "TRADE_DATE",
        "EXPLAIN", "CLOSE_PRICE", "CHANGE_RATE", "TURNOVERRATE",
        "BILLBOARD_NET_AMT", "BILLBOARD_BUY_AMT", "BILLBOARD_SELL_AMT",
        "BILLBOARD_DEAL_AMT", "ACCUM_AMOUNT", "DEAL_NET_RATIO",
        "DEAL_AMOUNT_RATIO", "FREE_MARKET_CAP", "EXPLANATION", "MARKET",
        "TRADE_ID", "TRADE_MARKET", "SECURITY_TYPE_CODE",
    ]
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/stock/tradedetail.html",
}


def api_get(params: dict[str, str], retries: int = 2) -> dict[str, Any]:
    """请求东方财富公开数据接口，执行有限重试并校验响应。"""
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("success"):
                raise RuntimeError(f"API 返回失败: {payload.get('message', 'unknown error')}")
            return payload
        except Exception as exc:  # 外部接口错误统一在 CLI 层报告
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def fetch_summary_page(date: str, page: int = 1, page_size: int = 200) -> list[dict]:
    payload = api_get(
        {
            "reportName": SUMMARY_REPORT,
            "columns": SUMMARY_COLUMNS,
            "source": "WEB",
            "client": "WEB",
            "filter": f"(TRADE_DATE<='{date}')(TRADE_DATE>='{date}')",
            "sortColumns": "SECURITY_CODE,TRADE_ID",
            "sortTypes": "1,1",
            "pageSize": str(page_size),
            "pageNumber": str(page),
        }
    )
    return (payload.get("result") or {}).get("data") or []


def fetch_summary(date: str) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        current = fetch_summary_page(date, page=page)
        if not current:
            break
        rows.extend(current)
        if len(current) < 200:
            break
        page += 1
    return rows


def fetch_seats(code: str, date: str, direction: str) -> list[dict]:
    """获取一只股票指定方向的全部上榜席位（可能包含多个上榜原因）。"""
    report_name, sort_column = DETAIL_REPORTS[direction]
    payload = api_get(
        {
            "reportName": report_name,
            "columns": "ALL",
            "filter": f"(TRADE_DATE='{date}')(SECURITY_CODE=\"{code}\")",
            "pageNumber": "1",
            "pageSize": "500",
            "sortTypes": "-1",
            "sortColumns": sort_column,
            "source": "WEB",
            "client": "WEB",
        }
    )
    return (payload.get("result") or {}).get("data") or []


def number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_seats(rows: list[dict], direction: str) -> dict[str, list[dict]]:
    """按 TRADE_ID 归组席位，避免多上榜原因互相混淆。"""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        trade_id = str(row.get("TRADE_ID") or "")
        grouped.setdefault(trade_id, []).append(
            {
                "operate_dept_code": str(row.get("OPERATEDEPT_CODE") or ""),
                "operate_dept_name": row.get("OPERATEDEPT_NAME") or "",
                "buy_amount_yuan": number(row.get("BUY")),
                "sell_amount_yuan": number(row.get("SELL")),
                "net_amount_yuan": number(row.get("NET")),
                "buy_ratio": number(row.get("TOTAL_BUYRIO")),
                "sell_ratio": number(row.get("TOTAL_SELLRIO")),
                "reason": row.get("EXPLANATION") or "",
            }
        )
    amount_key = "buy_amount_yuan" if direction == "buy" else "sell_amount_yuan"
    for seats in grouped.values():
        seats.sort(key=lambda item: item[amount_key] or 0, reverse=True)
        for rank, seat in enumerate(seats, start=1):
            seat["rank"] = rank
    return grouped


def build_stocks(
    summary_rows: list[dict],
    details: dict[str, dict[str, list[dict]]] | None = None,
    detail_errors: list[dict] | None = None,
) -> list[dict]:
    """构建股票→上榜原因→买卖席位层级，保留每个 TRADE_ID 的金额口径。"""
    stocks: dict[str, dict] = {}
    details = details or {}
    failed = {(item["code"], item["direction"]) for item in (detail_errors or [])}

    for row in summary_rows:
        code = str(row.get("SECURITY_CODE") or "")
        if not code:
            continue
        stock = stocks.setdefault(
            code,
            {
                "code": code,
                "secucode": row.get("SECUCODE") or "",
                "name": row.get("SECURITY_NAME_ABBR") or "",
                "market": row.get("MARKET") or "",
                "trade_market": row.get("TRADE_MARKET") or "",
                "security_type_code": row.get("SECURITY_TYPE_CODE") or "",
                "close": number(row.get("CLOSE_PRICE")),
                "change_rate": number(row.get("CHANGE_RATE")),
                "turnover_rate": number(row.get("TURNOVERRATE")),
                "market_amount_yuan": number(row.get("ACCUM_AMOUNT")),
                "free_market_cap_yuan": number(row.get("FREE_MARKET_CAP")),
                "rankings": [],
            },
        )
        trade_id = str(row.get("TRADE_ID") or "")
        buy_seats = details.get(code, {}).get("buy", {}).get(trade_id, [])
        sell_seats = details.get(code, {}).get("sell", {}).get(trade_id, [])
        stock["rankings"].append(
            {
                "trade_id": trade_id,
                "reason": row.get("EXPLANATION") or "",
                "summary": row.get("EXPLAIN") or "",
                "buy_total_yuan": number(row.get("BILLBOARD_BUY_AMT")),
                "sell_total_yuan": number(row.get("BILLBOARD_SELL_AMT")),
                "net_amount_yuan": number(row.get("BILLBOARD_NET_AMT")),
                "deal_amount_yuan": number(row.get("BILLBOARD_DEAL_AMT")),
                "buy_seats": buy_seats,
                "sell_seats": sell_seats,
                "detail_complete": len(buy_seats) >= 5
                and len(sell_seats) >= 5
                and (code, "buy") not in failed
                and (code, "sell") not in failed,
            }
        )
    return list(stocks.values())


def fetch_all_details(
    codes: list[str], date: str, workers: int = 6
) -> tuple[dict[str, dict[str, dict[str, list[dict]]]], list[dict]]:
    details: dict[str, dict[str, dict[str, list[dict]]]] = {}
    errors: list[dict] = []
    jobs = [(code, direction) for code in codes for direction in DETAIL_REPORTS]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_seats, code, date, direction): (code, direction)
            for code, direction in jobs
        }
        for future in as_completed(futures):
            code, direction = futures[future]
            try:
                details.setdefault(code, {})[direction] = normalize_seats(
                    future.result(), direction
                )
            except Exception as exc:
                errors.append(
                    {"code": code, "direction": direction, "error": str(exc)}
                )
    return details, sorted(errors, key=lambda item: (item["code"], item["direction"]))


def make_payload(
    date: str, summary_rows: list[dict], full: bool = True, workers: int = 6
) -> dict:
    codes = sorted({str(row.get("SECURITY_CODE") or "") for row in summary_rows if row.get("SECURITY_CODE")})
    details: dict = {}
    errors: list[dict] = []
    if full:
        details, errors = fetch_all_details(codes, date, workers=workers)
    stocks = build_stocks(summary_rows, details=details, detail_errors=errors)
    ranking_count = sum(len(stock["rankings"]) for stock in stocks)
    incomplete = sum(
        not ranking["detail_complete"]
        for stock in stocks
        for ranking in stock["rankings"]
    )
    return {
        "date": date,
        "source": "东方财富数据中心公开 API",
        "reports": [SUMMARY_REPORT] + (list(report for report, _ in DETAIL_REPORTS.values()) if full else []),
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_scope": "龙虎榜汇总及买卖前五席位" if full else "龙虎榜汇总",
        "complete": full and not errors and incomplete == 0,
        "stock_count": len(stocks),
        "ranking_count": ranking_count,
        "incomplete_ranking_count": incomplete,
        "errors": errors,
        "stocks": stocks,
    }


def fmt_wan(value: float | int | None) -> str:
    return "—" if value is None else f"{value / 1e4:+.0f}"


def fmt_pct(value: float | int | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def to_markdown(payload: dict) -> str:
    stocks = payload["stocks"]
    changes = [stock["change_rate"] for stock in stocks if stock["change_rate"] is not None]
    lines = [
        f"# 龙虎榜数据 · {payload['date']}",
        "",
        f"> 数据来源: {payload['source']}",
        f"> 抓取时间: {payload['fetched_at']}",
        f"> 股票 {payload['stock_count']} 只 / 上榜记录 {payload['ranking_count']} 条 / 完整: {payload['complete']}",
        "",
        "## 涨跌分布",
        "",
        f"上涨 {sum(value > 0 for value in changes)} 只 / 下跌 {sum(value < 0 for value in changes)} 只 / 涨幅≥9.9% {sum(value >= 9.9 for value in changes)} 只 / 跌幅≤-9.9% {sum(value <= -9.9 for value in changes)} 只",
        "",
        "## 上榜记录与席位",
        "",
    ]
    for stock in stocks:
        lines.extend(
            [
                f"### {stock['code']} {stock['name']}",
                "",
                f"收盘价 {stock['close']} / 涨跌幅 {fmt_pct(stock['change_rate'])} / 换手率 {fmt_pct(stock['turnover_rate'])}",
                "",
            ]
        )
        for ranking in stock["rankings"]:
            lines.extend(
                [
                    f"#### {ranking['reason'] or '上榜原因信息不足'}",
                    "",
                    f"买入 {fmt_wan(ranking['buy_total_yuan'])} 万元 / 卖出 {fmt_wan(ranking['sell_total_yuan'])} 万元 / 净额 {fmt_wan(ranking['net_amount_yuan'])} 万元",
                    "",
                    "| 方向 | 排名 | 营业部 | 买入(万) | 卖出(万) | 净额(万) |",
                    "|---|---:|---|---:|---:|---:|",
                ]
            )
            for direction, seats in (("买", ranking["buy_seats"]), ("卖", ranking["sell_seats"])):
                for seat in seats:
                    lines.append(
                        f"| {direction} | {seat['rank']} | {seat['operate_dept_name']} | {fmt_wan(seat['buy_amount_yuan'])} | {fmt_wan(seat['sell_amount_yuan'])} | {fmt_wan(seat['net_amount_yuan'])} |"
                    )
            if not ranking["detail_complete"]:
                lines.append("| 信息不足 | — | 该上榜原因的买卖席位未完整返回 | — | — | — |")
            lines.append("")
    if payload["errors"]:
        lines.extend(["## 抓取错误", "", "```json", json.dumps(payload["errors"], ensure_ascii=False, indent=2), "```", ""])
    lines.append("> 龙虎榜为滞后公开数据，不构成投资建议。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="获取东方财富龙虎榜汇总及买卖前五席位。")
    parser.add_argument("date", help="交易日期，格式 YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认 Markdown）")
    parser.add_argument("--summary-only", action="store_true", help="仅获取汇总，不请求买卖席位")
    parser.add_argument("--workers", type=int, default=6, help="明细并发数，默认 6，范围 1-12")
    args = parser.parse_args()

    date = args.date.strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        print(f"错误: 日期格式应为 YYYY-MM-DD，收到 {date!r}", file=sys.stderr)
        return 1
    if not 1 <= args.workers <= 12:
        print("错误: --workers 必须在 1 到 12 之间", file=sys.stderr)
        return 1

    print(f"正在获取 {date} 龙虎榜汇总...", file=sys.stderr)
    try:
        rows = fetch_summary(date)
    except Exception as exc:
        print(f"获取汇总失败: {exc}", file=sys.stderr)
        return 1
    if not rows:
        print(f"警告: {date} 无龙虎榜数据（可能非交易日或尚未发布）", file=sys.stderr)
        return 1

    codes = {row.get("SECURITY_CODE") for row in rows if row.get("SECURITY_CODE")}
    mode = "汇总" if args.summary_only else "完整席位"
    print(f"汇总 {len(rows)} 条 / {len(codes)} 只股票，正在构建{mode}数据...", file=sys.stderr)
    payload = make_payload(date, rows, full=not args.summary_only, workers=args.workers)
    print(
        f"完成: {payload['ranking_count']} 条上榜记录，未完整 {payload['incomplete_ranking_count']} 条，接口错误 {len(payload['errors'])} 个",
        file=sys.stderr,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else to_markdown(payload))
    return 0 if not payload["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())
