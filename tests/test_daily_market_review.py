import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "daily-market-review" / "scripts" / "fetch_market_review.py"
SPEC = importlib.util.spec_from_file_location("fetch_market_review", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def row(code, name, change, industry, close=10, high=10, prev=10):
    return {"f12": code, "f14": name, "f2": close, "f3": change, "f5": 1, "f6": 100, "f8": 2, "f15": high, "f16": 9, "f17": 9.5, "f18": prev, "f20": 1000, "f21": 800, "f100": industry, "f102": "测试"}


def test_limit_thresholds():
    assert MODULE.limit_pct("600001", "普通") == 10
    assert MODULE.limit_pct("300001", "普通") == 20
    assert MODULE.limit_pct("600001", "ST测试") == 5


def test_build_payload_schema(monkeypatch):
    monkeypatch.setattr(MODULE, "enrich_candidate", lambda stock, date: {**stock, "board_height": 1, "volume_ratio_5d": 2, "breakout_previous_5d_high": True})
    rows = [row("600001", "甲", 10, "行业A"), row("600002", "乙", 10, "行业A"), row("600003", "丙", 10, "行业A"), row("600004", "丁", -10, "行业B")]
    payload = MODULE.build_payload("2026-08-10", rows, {})
    assert payload["schema_version"] == "daily-market-review/v1"
    assert payload["sentiment"]["limit_up_count"] == 3
    assert payload["sentiment"]["limit_down_count"] == 1
    assert payload["boards"]["items"][0]["classification"] == "主线候选"
    assert len(payload["stocks"]) == 4
