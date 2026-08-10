from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "longhubang-analysis" / "scripts" / "fetch_longhubang.py"
SPEC = importlib.util.spec_from_file_location("fetch_longhubang", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def summary_row(trade_id: str, reason: str, net: int) -> dict:
    return {
        "SECURITY_CODE": "000001",
        "SECUCODE": "000001.SZ",
        "SECURITY_NAME_ABBR": "示例",
        "MARKET": "SZ",
        "TRADE_MARKET": "深交所主板",
        "SECURITY_TYPE_CODE": "058001001",
        "CLOSE_PRICE": 10.5,
        "CHANGE_RATE": 9.99,
        "TURNOVERRATE": 12.3,
        "BILLBOARD_NET_AMT": net,
        "BILLBOARD_BUY_AMT": 30_000,
        "BILLBOARD_SELL_AMT": 20_000,
        "BILLBOARD_DEAL_AMT": 50_000,
        "ACCUM_AMOUNT": 100_000,
        "FREE_MARKET_CAP": 1_000_000,
        "EXPLANATION": reason,
        "EXPLAIN": "机构参与",
        "TRADE_ID": trade_id,
    }


def seat_row(trade_id: str, name: str, buy: int, sell: int) -> dict:
    return {
        "TRADE_ID": trade_id,
        "OPERATEDEPT_CODE": "100",
        "OPERATEDEPT_NAME": name,
        "BUY": buy,
        "SELL": sell,
        "NET": buy - sell,
        "TOTAL_BUYRIO": 0.1,
        "TOTAL_SELLRIO": 0.05,
        "EXPLANATION": "测试原因",
    }


def five_seats(trade_id: str, direction: str) -> list[dict]:
    return [
        seat_row(
            trade_id,
            f"{direction}{index}",
            100 - index if direction == "买" else 0,
            100 - index if direction == "卖" else 0,
        )
        for index in range(5)
    ]


class FetchLonghubangTest(unittest.TestCase):
    def test_normalize_seats_groups_by_trade_id_and_ranks(self) -> None:
        grouped = MODULE.normalize_seats(
            [
                seat_row("A", "席位二", 100, 0),
                seat_row("A", "席位一", 200, 0),
                seat_row("B", "另一原因", 300, 0),
            ],
            "buy",
        )

        self.assertEqual(set(grouped), {"A", "B"})
        self.assertEqual(grouped["A"][0]["operate_dept_name"], "席位一")
        self.assertEqual(grouped["A"][0]["rank"], 1)

    def test_build_stocks_preserves_multiple_ranking_amounts(self) -> None:
        rows = [
            summary_row("A", "涨幅偏离", 10_000),
            summary_row("B", "换手率", -20_000),
        ]
        details = {
            "000001": {
                "buy": {
                    "A": MODULE.normalize_seats(five_seats("A", "买"), "buy")["A"],
                    "B": MODULE.normalize_seats(five_seats("B", "买"), "buy")["B"],
                },
                "sell": {
                    "A": MODULE.normalize_seats(five_seats("A", "卖"), "sell")["A"],
                    "B": MODULE.normalize_seats(five_seats("B", "卖"), "sell")["B"],
                },
            }
        }

        stocks = MODULE.build_stocks(rows, details=details)

        self.assertEqual(len(stocks), 1)
        self.assertEqual(len(stocks[0]["rankings"]), 2)
        self.assertEqual(stocks[0]["rankings"][0]["net_amount_yuan"], 10_000)
        self.assertEqual(stocks[0]["rankings"][1]["net_amount_yuan"], -20_000)
        self.assertTrue(all(item["detail_complete"] for item in stocks[0]["rankings"]))

    def test_payload_has_provenance_without_future_return_fields(self) -> None:
        details = {
            "000001": {
                "buy": {"A": MODULE.normalize_seats([seat_row("A", "买A", 100, 0)], "buy")["A"]},
                "sell": {"A": MODULE.normalize_seats(five_seats("A", "卖"), "sell")["A"]},
            }
        }
        details["000001"]["buy"]["A"] = MODULE.normalize_seats(
            five_seats("A", "买"), "buy"
        )["A"]
        with patch.object(MODULE, "fetch_all_details", return_value=(details, [])):
            payload = MODULE.make_payload("2026-08-10", [summary_row("A", "涨幅偏离", 10_000)])

        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["stock_count"], 1)
        self.assertIn("RPT_BILLBOARD_DAILYDETAILSBUY", payload["reports"])
        self.assertNotIn("D1_CLOSE", encoded)
        self.assertNotIn('"d1"', encoded)

    def test_fewer_than_five_seats_is_incomplete(self) -> None:
        details = {
            "000001": {
                "buy": {"A": MODULE.normalize_seats(five_seats("A", "买")[:4], "buy")["A"]},
                "sell": {"A": MODULE.normalize_seats(five_seats("A", "卖"), "sell")["A"]},
            }
        }
        stocks = MODULE.build_stocks([summary_row("A", "涨幅偏离", 10_000)], details)
        self.assertFalse(stocks[0]["rankings"][0]["detail_complete"])

    def test_markdown_uses_threshold_not_limit_up_label(self) -> None:
        payload = MODULE.make_payload(
            "2026-08-10", [summary_row("A", "涨幅偏离", 10_000)], full=False
        )
        markdown = MODULE.to_markdown(payload)
        self.assertIn("涨幅≥9.9%", markdown)
        self.assertNotIn("涨停 1只", markdown)

    def test_invalid_date_exits_without_network(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "20260810"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("YYYY-MM-DD", result.stderr)


if __name__ == "__main__":
    unittest.main()
