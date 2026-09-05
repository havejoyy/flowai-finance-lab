# FlowAI Finance Skills

面向中文用户的开源 AI 金融研究 Skill 集合。它们用于整理信息、检查证据和复盘决策过程，不提供荐股、收益承诺或自动交易。

## Skills

| Skill | 用途 |
|---|---|
| [`daily-market-review`](skills/daily-market-review/) | 自动取数、标准 JSON 与七步结构化A股盘后复盘 |
| [`financial-report-analysis`](skills/financial-report-analysis/) | 上市公司财报事实提取与风险检查 |
| [`longhubang-analysis`](skills/longhubang-analysis/) | 真实龙虎榜席位解读、结构信号与五层证据验证 |
| [`youzi-market-research`](skills/youzi-market-research/) | 五层漏斗式短线市场证据研究 |
| [`stock-character-analysis`](skills/stock-character-analysis/) | 用波动、趋势、流动性、活跃度和回撤恢复生成股性画像 |

## 一键安装

```bash
# 查看可安装 Skill
npx skills add havejoyy/flowai-finance-lab --list

# 全部安装到 Codex
npx skills add havejoyy/flowai-finance-lab --skill '*' --agent codex --global --yes

# 只安装财报分析
npx skills add havejoyy/flowai-finance-lab --skill financial-report-analysis --agent codex --global --yes

# 只安装龙虎榜分析（包含真实数据抓取脚本）
npx skills add havejoyy/flowai-finance-lab --skill longhubang-analysis --agent codex --global --yes

# 只安装股性分析（输入股票代码即可使用）
npx skills add havejoyy/flowai-finance-lab --skill stock-character-analysis --agent codex --global --yes

# 联合安装每日复盘和龙虎榜分析
npx skills add havejoyy/flowai-finance-lab --skill daily-market-review --skill longhubang-analysis --agent codex --global --yes
```

其他常见 Agent：

```bash
npx skills add havejoyy/flowai-finance-lab --skill '*' --agent claude-code --global --yes
npx skills add havejoyy/flowai-finance-lab --skill '*' --agent cursor --global --yes
npx skills add havejoyy/flowai-finance-lab --skill '*' --agent trae-cn --global --yes
```

临时使用而不安装：

```bash
npx skills use havejoyy/flowai-finance-lab --skill financial-report-analysis --agent codex
npx skills use havejoyy/flowai-finance-lab --skill stock-character-analysis --agent codex
```

更新已安装 Skill：

```bash
npx skills update
```

## 每日市场复盘与龙虎榜联动（安装即可用）

两个 Skill 都包含随包安装的纯 Python 标准库脚本，无需 AkShare、pandas 或 requests。用户可以直接对 Agent 说：

```text
调用 daily-market-review 和 longhubang-analysis，
联合分析 2026-08-11 的市场情绪、主线、龙头和龙虎榜。
```

Agent 会获取全市场收盘快照和龙虎榜买卖前五，按证券代码关联后再进行证据化分析。也可以从仓库根目录直接运行：

```bash
# 获取最近交易日市场快照，输出 daily-market-review/v1 JSON
python3 skills/daily-market-review/scripts/fetch_market_review.py 2026-08-11 --json

# 一键获取市场快照和龙虎榜，输出 market-longhubang-linked/v1 JSON
python3 skills/daily-market-review/scripts/run_linked_review.py 2026-08-11 --json

# 获取龙虎榜数据，默认输出 Markdown
python3 skills/longhubang-analysis/scripts/fetch_longhubang.py 2026-08-11

# 保存到文件，直接喂给 Skill 解读
python3 skills/longhubang-analysis/scripts/fetch_longhubang.py 2026-08-11 > lhb-2026-08-11.md

# 输出 JSON 格式
python3 skills/longhubang-analysis/scripts/fetch_longhubang.py 2026-08-11 --json
```

安装后可从 Agent 的全局 Skill 目录运行相同脚本。Agent 在用户只提供交易日期时，会按 Skill 规则自动取数，不要求用户手工准备 JSON。

### 标准输出与数据边界

- `daily-market-review/v1`：指数、市场涨跌与成交、涨跌停和炸板推算、行业强度、个股横截面及代表性候选。
- `market-longhubang-linked/v1`：保留完整市场快照和龙虎榜，并在 `cross_validation` 中按证券代码提供行业、量价和板块上下文。
- `longhubang-analysis` 按 `TRADE_ID` 保留同股不同上榜原因，避免跨口径重复累计。
- 全市场横截面接口只用于最近交易日；日期校验失败时会拒绝把实时行情冒充历史行情。
- “主线候选”和“代表性标的”是规则筛选结果，不等同于经人工确认的持续主线或市场龙头。
- 指数、历史K线或席位接口部分失败时，输出会保留 `complete`、`errors` 和缺失字段，不补造行情。

数据来源：东方财富公开行情与数据中心接口。龙虎榜使用 `--summary-only` 可降级为快速汇总模式。

## 股性分析（输入代码即可用）

`stock-character-analysis` 把“股性”拆成波动、趋势、流动性、活跃度和回撤恢复五组可验证数据。它用于理解股票在指定历史窗口内的交易特征，不预测未来涨跌，也不提供买卖点。

安装后直接告诉 Agent：

```text
调用 stock-character-analysis，分析 600519 最近的股性。
```

也可以从仓库根目录运行脚本：

```bash
# 自动获取截至最近交易日的前复权日线
python3 skills/stock-character-analysis/scripts/analyze_stock_character.py 600519 --json

# 指定截止日期
python3 skills/stock-character-analysis/scripts/analyze_stock_character.py 600519 --end 2026-09-04 --json

# 使用本地日线 CSV
python3 skills/stock-character-analysis/scripts/analyze_stock_character.py --input daily.csv --json
```

输出遵循 `stock-character-analysis/v1`，包含数据来源、观察日期、样本数、五维指标、阶段标签、标签证据和限制条件。完整口径见 [股性分析使用说明](docs/stock-character-analysis.md)。

## 本地验证

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s tests -v
npx skills add . --list
```

## 开源协议与边界

代码、Skill 指令和参考资料统一采用 [Apache License 2.0](LICENSE)。FlowAI Lab 名称和品牌标识不因该协议而授予商标使用权。

使用前请阅读 [免责声明](DISCLAIMER.md)。任何示例均为方法演示，不代表对相关证券的当前判断。

## 参与贡献

欢迎通过 Issue 报告错误、数据口径问题和风险遗漏，通过 Pull Request 改进框架。提交前请阅读 [贡献指南](CONTRIBUTING.md) 并运行本地验证。
