# 股性分析 Skill 使用说明

`stock-character-analysis` 用公开A股日线或用户提供的 CSV，生成有日期、有来源、有指标依据的阶段性股性画像。

## 它解决什么问题

“活跃”“趋势好”“股性妖”等说法常把价格事实、行情阶段和主观判断混在一起。本 Skill 将观察拆成五个维度：

- 波动：日收益年化波动率、ATR占比、平均振幅；
- 趋势：20日收益和方向效率；
- 流动性：成交额中位数、成交稳定性和可选换手率；
- 活跃度：大幅波动与跳空出现频率；
- 回撤恢复：60日最大回撤和当前距阶段高点距离。

这些指标用于回答“这只股票通常以什么方式波动、持有压力来自哪里”，不用于判断股票质量或预测未来走势。

## 开箱使用

安装：

```bash
npx skills add havejoyy/flowai-finance-lab --skill stock-character-analysis --agent codex --global --yes
```

自然语言调用：

```text
调用 stock-character-analysis，分析 600519 最近的股性。
```

命令行调用：

```bash
python3 skills/stock-character-analysis/scripts/analyze_stock_character.py 600519 --json
```

脚本默认获取截至最近交易日的前120根前复权日线。指定截止日期：

```bash
python3 skills/stock-character-analysis/scripts/analyze_stock_character.py 600519 --end 2026-09-04 --json
```

使用本地 CSV：

```bash
python3 skills/stock-character-analysis/scripts/analyze_stock_character.py --input daily.csv --json
```

CSV 必须包含 `date/open/close/high/low/volume/amount`，可选 `turnover_rate`。至少需要61根日线。

## 输出内容

JSON 的 `schema` 为 `stock-character-analysis/v1`，主要字段包括：

- `security_code`、`security_name`、`as_of`、`source`、`adjustment`、`sample_size`；
- `metrics`：所有计算后的核心指标；
- `dimensions`：五个维度的人话证据；
- `labels` 与 `label_evidence`：允许并存的阶段标签及其依据；
- `limitations`：数据和推断边界。

当前标签包括稳定趋势、高波动趋势、低波动震荡、高频脉冲、高弹性、流动性偏弱，以及证据不足时的“混合型/暂不定型”。阈值详见 [`stock-character-framework.md`](../skills/stock-character-analysis/references/stock-character-framework.md)。

## 数据与边界

- 在线模式读取东方财富公开行情接口，不需要账号、Cookie 或金融账户授权；
- 网络或接口失败时明确报错，不生成缺少证据的结论；
- 标签只描述当前观察窗口，窗口变化后需要重新分析；
- 成交额只是流动性代理，不能替代盘口深度；
- 价格与成交行为不能证明“主力”“庄家”或具体游资的意图；
- 不输出买卖点、目标价、止损价、仓位建议或收益承诺。

## 本地验证

```bash
python3 scripts/validate_skills.py
python3 -m unittest tests.test_stock_character_analysis -v
```
