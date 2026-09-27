# 输入规范

提取脚本输出 `sources.json`，key 为 PDF 文件名（无扩展名）。页码都是从1开始的 PDF 物理页，不是印刷页码。compare.py 只使用标准库。数据文件 UTF-8，金额建议用字符串避免浮点误差。

```json
{
  "company": "示例公司", "code": "000000",
  "current_period": ["2024-01-01", "2024-12-31"],
  "prior_period": ["2023-01-01", "2023-12-31"],
  "documents": {
    "new": {"source_id": "new", "company": "示例公司", "code": "000000", "report_type": "annual", "period": ["2024-01-01", "2024-12-31"], "url": "https://example.org/new.pdf", "retrieved_at": "2026-09-26", "audit": "标准无保留意见"},
    "old": {"source_id": "old", "company": "示例公司", "code": "000000", "report_type": "annual", "period": ["2023-01-01", "2023-12-31"], "url": "https://example.org/old.pdf", "retrieved_at": "2026-09-26", "audit": "标准无保留意见"}
  },
  "metrics": [{
    "id": "revenue", "label": "营业收入", "kind": "flow", "comparable": true,
    "current": {"value": "120.00", "unit": "亿元", "currency": "CNY", "scope": "consolidated", "period": ["2024-01-01", "2024-12-31"], "source": {"doc": "new", "page": 7, "quote": "营业收入 120.00 100.00"}},
    "baseline": {"value": "100.00", "unit": "亿元", "currency": "CNY", "scope": "consolidated", "period": ["2023-01-01", "2023-12-31"], "source": {"doc": "new", "page": 7, "quote": "营业收入 120.00 100.00"}},
    "prior_original": {"value": "100.00", "unit": "亿元", "currency": "CNY", "scope": "consolidated", "period": ["2023-01-01", "2023-12-31"], "source": {"doc": "old", "page": 7, "quote": "营业收入 100.00"}},
    "explanation": null,
    "question": "收入变化主要来自销量、价格还是合并范围？"
  }]
}
```

- `documents` 必须且仅有 new/old，两份报告公司的名字和代码与根级相同、类型相同（annual/half-year/quarterly），period 与根级相应期间相同。根级期间须为相邻年度同起止月日。
- observation 的 `period`：flow/ratio 用起止两个日期；stock 用期末一个日期。`scope` 为 consolidated/parent；三份观察须一致。合并范围变动对可比性的影响需 Agent 核实，不能依靠脚本的 scope 字符串。
- 单位：元/万元/百万元/亿元 自动归一到元；`percent` 按百分点计算；元/股允许同单位对比。缺失值用 null（不要用0填补），保留 question。
- `current` 和 `baseline` 必须来自 new；`prior_original` 必须来自 old。
- `source.quote` 是所在页中包含该数字的最短完整指标行（可跨换行）。脚本按忽略空白匹配原文并验证数字；表头、行列对应、币种单位与公式定义由 Agent 看原页复核。
- `comparable` 必须明确为 true 才计算。无法确认时 false，写 `comparability_note`。
- 新报告 baseline 与旧报告 prior_original 不同：如果已确认是重述并可比，提供 `restatement: {"confirmed": true, "source": {"doc": "new", "page": 8, "quote": "原文中的重述说明"}}`。否则不计算，标待核实；确认后用新报告可比基数计算，仍保留原值及差额。
- `explanation` 可为 `{"summary": "公司披露的原因摘要", "source": {"doc": "new", "page": 35, "quote": "原文片段"}}`。摘要不经脚本语义验证，Agent 必须区分披露与推断。
- 提取不完整的行仍保留为未核实；不能因为缺数静默丢掉关键指标。报告只覆盖输入指标，不声称全报扫描无遗漏。
