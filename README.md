# FlowAI Finance Skills

面向中文用户的开源 AI 金融研究 Skill 集合。它们用于整理信息、检查证据和复盘决策过程，不提供荐股、收益承诺或自动交易。

## Skills

| Skill | 用途 |
|---|---|
| [`panic-pressure-index`](skills/panic-pressure-index/) | 自动获取公开月度行情，计算恐慌压力总分及三分项；注明日期和公开版口径 |
| [`daily-market-review`](skills/daily-market-review/) | 自动取数、标准 JSON 与七步结构化A股盘后复盘 |
| [`financial-report-analysis`](skills/financial-report-analysis/) | 上市公司财报事实提取与风险检查 |
| [`financial-report-changes`](skills/financial-report-changes/) | 两期财报变化核验、公司解释与待查清单；[教程与下载](docs/financial-report-changes.md) |
| [`longhubang-analysis`](skills/longhubang-analysis/) | 真实龙虎榜席位解读、结构信号与五层证据验证 |
| [`youzi-market-research`](skills/youzi-market-research/) | 五层漏斗式短线市场证据研究 |
| [`stock-character-analysis`](skills/stock-character-analysis/) | 用波动、趋势、流动性、活跃度和回撤恢复生成股性画像 |

## 财报变化追踪

[下载独立Skill包](downloads/financial-report-changes-skill.zip) · [使用教程](docs/financial-report-changes.md) · [可复制模板](docs/financial-report-changes-prompt.txt)

输入两期同类财报，先核对三指标，再查附注解释与待核实问题。

## 恐慌压力指标 Skill（自动取数）

[独立Skill下载](downloads/panic-pressure-index-skill.zip) · [完整使用说明](skills/panic-pressure-index/references/public-data.md)

自动获取公开行情，输出0—100恐慌压力总分、强度分、扩散分、抛压分及实际数据日期。无需上传交易记录、准备行情、配置API密钥，也不依赖作者维护数据服务。

```bash
# 安装到 Codex
npx skills add havejoyy/flowai-finance-lab --skill panic-pressure-index --agent codex --global --yes
```

安装后，对支持本地脚本执行的AI工具说：

```text
调用 panic-pressure-index，计算最近可用交易日的恐慌压力，
给出总分、三个分项和实际数据日期。
```

也可从仓库根目录运行：

```bash
python3 skills/panic-pressure-index/scripts/panic_pressure.py
python3 skills/panic-pressure-index/scripts/panic_pressure.py --date 2026-09-30 --json
# 明确使用已下载缓存，不检查网络更新
python3 skills/panic-pressure-index/scripts/panic_pressure.py --offline
```

**环境与首次运行：** Python 3.9+、联网和数GB磁盘空间；需要时自动在专用虚拟环境安装numpy/h5py，不修改系统Python。首次下载约1GB，初始化400个交易日；默认缓存位于 `~/.cache/panic-pressure-index`，可通过 `--cache-dir`指定。已在macOS上实测；其他系统尚未验收。

**后续运行：** 检查公开源更新，相同数据包校验并复用原始因子缓存，重新计算排名及总分；发现新包后重建整批历史，避免拼接不同股票池或供应商修订版本。

**真实测试（2026-10-17执行，行情日2026-09-30）：** 从空缓存下载公开数据，对5188只存续股票、400个交易日计算；当日有效样本4972只，各因子399个历史参考日。总分 **67.4**，强度 **88.6**、扩散 **59.8**、抛压 **53.9**。

**请先了解这两个限制：**

- **上游数据包按月更新，不是每日或实时行情。** 输出中的数据日期才是指标对应日期；指定日期缺失或数据不合格时返回无可用分数，不冒用旧日期。
- **公开版CN_PANIC_PUBLIC_V1用当前存续股票回看历史，存在幸存者偏差。** 六因子公式保留，但股票池和参考样本与原版本不同，不能直接比较或拼接分数。它是研究试验指标，不提供买卖信号。

软件授权不包含供应商数据的额外使用或转授权许可。行情只下载到用户本地缓存，不随仓库或Skill安装包分发。

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
