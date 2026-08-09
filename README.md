# FlowAI Finance Skills

面向中文用户的开源 AI 金融研究 Skill 集合。它们用于整理信息、检查证据和复盘决策过程，不提供荐股、收益承诺或自动交易。

## Skills

| Skill | 用途 |
|---|---|
| [`daily-market-review`](skills/daily-market-review/) | 七步结构化A股盘后复盘 |
| [`financial-report-analysis`](skills/financial-report-analysis/) | 上市公司财报事实提取与风险检查 |
| [`longhubang-analysis`](skills/longhubang-analysis/) | 六步龙虎榜席位解读与游资意图推演 |
| [`youzi-market-research`](skills/youzi-market-research/) | 五层漏斗式短线市场证据研究 |

## 一键安装

```bash
# 查看可安装 Skill
npx skills add havejoyy/flowai-finance-lab --list

# 全部安装到 Codex
npx skills add havejoyy/flowai-finance-lab --skill '*' --agent codex --global --yes

# 只安装财报分析
npx skills add havejoyy/flowai-finance-lab --skill financial-report-analysis --agent codex --global --yes
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
```

更新已安装 Skill：

```bash
npx skills update
```

## 龙虎榜数据获取（安装即可用）

龙虎榜 Skill 自带数据获取脚本，纯 Python 标准库实现，无需安装第三方包：

```bash
# 获取当日龙虎榜数据，输出 Markdown
python3 scripts/fetch_longhubang.py 2026-08-07

# 保存到文件，直接喂给 Skill 解读
python3 scripts/fetch_longhubang.py 2026-08-07 > lhb-2026-08-07.md

# 输出 JSON 格式
python3 scripts/fetch_longhubang.py 2026-08-07 --json
```

数据来源：东方财富数据中心公开 API。输出包含涨跌分布、净买入 TOP20、净卖出 TOP20、全部个股明细和席位摘要。

## 本地验证

```bash
python3 scripts/validate_skills.py
npx skills add . --list
```

## 开源协议与边界

代码、Skill 指令和参考资料统一采用 [Apache License 2.0](LICENSE)。FlowAI Lab 名称和品牌标识不因该协议而授予商标使用权。

使用前请阅读 [免责声明](DISCLAIMER.md)。任何示例均为方法演示，不代表对相关证券的当前判断。

## 参与贡献

欢迎通过 Issue 报告错误、数据口径问题和风险遗漏，通过 Pull Request 改进框架。提交前请阅读 [贡献指南](CONTRIBUTING.md) 并运行本地验证。
