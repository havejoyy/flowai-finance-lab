#!/usr/bin/env python3
"""一键获取每日市场复盘与龙虎榜，并按股票代码生成交叉验证数据。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MARKET_SCRIPT = Path(__file__).with_name("fetch_market_review.py")


def run_json(script: Path, date: str, extra: list[str] | None = None) -> dict:
    command = [sys.executable, str(script), date, "--json"] + (extra or [])
    proc = subprocess.run(command, text=True, capture_output=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{script.name} 未返回有效 JSON: {proc.stderr.strip()}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{script.name} 获取失败: {payload.get('errors')}")
    return payload


def locate_longhubang(explicit: str | None) -> Path:
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates += [ROOT / "longhubang-analysis/scripts/fetch_longhubang.py", Path.home() / ".agents/skills/longhubang-analysis/scripts/fetch_longhubang.py", Path.home() / ".codex/skills/longhubang-analysis/scripts/fetch_longhubang.py"]
    for candidate in candidates:
        if candidate.is_file(): return candidate
    raise FileNotFoundError("未找到 longhubang-analysis/scripts/fetch_longhubang.py；请安装该 Skill 或使用 --longhubang-script")


def link(market: dict, longhubang: dict) -> list[dict]:
    board_context = {board["name"]: board for board in market.get("boards", {}).get("items", [])}
    enriched = {stock["code"]: stock for stock in market.get("representative_stocks", [])}
    stock_context = {}
    for stock in market.get("stocks", []):
        board = board_context.get(stock.get("industry"))
        detail = enriched.get(stock["code"], stock)
        stock_context[stock["code"]] = {"industry": stock.get("industry"), "board_classification": board.get("classification") if board else None, "industry_limit_up_count": board.get("limit_up_count") if board else None, "industry_average_change_rate": board.get("average_change_rate") if board else None, "board_height": detail.get("board_height"), "volume_ratio_5d": detail.get("volume_ratio_5d"), "breakout_previous_5d_high": detail.get("breakout_previous_5d_high"), "change_rate": stock.get("change_rate"), "turnover_rate": stock.get("turnover_rate"), "amount_yuan": stock.get("amount_yuan")}
    linked = []
    for stock in longhubang.get("stocks", []):
        context = stock_context.get(stock["code"])
        linked.append({"code": stock["code"], "name": stock["name"], "longhubang_ranking_count": len(stock.get("rankings", [])), "market_context": context, "cross_validation_status": "matched" if context else "market_context_insufficient"})
    return linked


def main() -> int:
    parser = argparse.ArgumentParser(description="一键联动每日市场复盘与龙虎榜")
    parser.add_argument("date", help="交易日期 YYYY-MM-DD")
    parser.add_argument("--longhubang-script")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        market = run_json(MARKET_SCRIPT, args.date)
        longhubang = run_json(locate_longhubang(args.longhubang_script), args.date, ["--workers", str(args.workers)])
        payload = {"schema_version": "market-longhubang-linked/v1", "trade_date": args.date, "complete": bool(market.get("complete") and longhubang.get("complete")), "market_review": market, "longhubang": longhubang, "cross_validation": link(market, longhubang)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"schema_version": "market-longhubang-linked/v1", "trade_date": args.date, "complete": False, "errors": [{"error": str(exc)}]}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
