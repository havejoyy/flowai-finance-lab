from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "longhubang-analysis"


class LonghubangSkillContentTest(unittest.TestCase):
    def test_skill_requires_evidence_states_and_fixed_disclaimer(self) -> None:
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "证据充分/证据不足/不通过",
            "A/B/C仅表示五层证据适配度",
            "严禁把营业部确定归属于自然人",
            "席位与游资的映射基于公开市场传闻",
        ):
            self.assertIn(phrase, text)

    def test_mapping_reference_preserves_uncertainty(self) -> None:
        text = (SKILL_DIR / "references" / "seat-mapping-and-signals.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("未经官方证实", text)
        self.assertIn("不得称为“假机构”", text)
        self.assertIn("不能仅凭华鑫", text)
        self.assertIn("国泰海通证券南京太平南路", text)


if __name__ == "__main__":
    unittest.main()
