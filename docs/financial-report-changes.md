# 财报变化追踪 Skill：下载、使用教程与模板

给同一家非金融上市公司的两期同类财报，核对比较口径、计算变化、回查公司解释，输出带PDF页码的变化清单。

## 获取工具

- [下载独立Skill ZIP](../downloads/financial-report-changes-skill.zip)：打开后点击下载原始文件；无需下载全部仓库。
- [查看Skill源码](../skills/financial-report-changes/SKILL.md)
- [复制完整使用模板](financial-report-changes-prompt.txt)

ZIP内包括financial-report-changes目录、使用说明、试用提示词与Apache-2.0许可。解压后保留SKILL.md、scripts、references、agents的结构。

## 环境与三步使用

需要能读取本地文件并运行命令的Agent，以及Python3。仅在普通聊天窗口粘贴提示词不等于运行附带脚本。

1. 解压工具，准备同公司、相邻年度、同类的两份官方PDF，例如上年半年报和本年半年报。单季与年初累计口径不能混用。
2. 让Agent读取：`请读取［解压目录］/financial-report-changes/SKILL.md，按其中流程执行。` 首次检查依赖，提取文本需要`pypdf`：`python3 -m pip install pypdf`。遇乱码时可安装`pypdfium2`并切换引擎；扫描件需经复核的OCR或可检索版本。
3. 粘贴下面模板，替换PDF路径。首轮先核对三指标，再按证据决定是否继续拆解。

```text
请用 financial-report-changes Skill 对比：
旧报告：［旧PDF完整路径］
新报告：［新PDF完整路径］
先看营业收入、归母净利润、经营现金流净额。
核对公司、期间、单位、币种、合并口径和新旧比较基数，运行计算脚本。
选一项重点变化继续查附注，输出变化、PDF页码、公司解释和待查问题。
能量化则计算并保存计算过程；不能确认就标明，不补数据。
新旧比较基数不一致时保留旧值，核查重述后再决定能否同比。
返回变化清单、JSON结果与输入文件的保存位置，不给买卖建议。
```

## 应该得到什么

| 文件 | 用途 |
|---|---|
| `work/sources.json` | 逐页提取文本、解析引擎、源文件哈希 |
| `work/input.json` | Agent核对的数字、三列基数、出处与解释 |
| `work/result/comparison.json` | 校验状态和计算结果 |
| `work/result/变化清单.md` | 可阅读的变化清单与待核实问题 |

不同运行目录可以改变保存位置，由Agent在结果中说明。脚本成功退出不代表每行都可比；检查覆盖率与未核实项，再回原页核对表头、单位与数字。

## 匿名实测：比同比数字多查一步

某能源公司2026上半年经营现金流同比增长29.72%。进一步核对两期现金流补充资料发现：

| 经营现金流同比增量组成 | 人民币亿元 |
|---|---:|
| 合并净利润增加 | 162.76 |
| 固定资产折旧、油气资产折耗加回增加 | 31.51 |
| 经营性应付项目调整额增加 | 108.97 |
| 其他调节项目净变化 | 21.25 |
| 合计 | 324.49 |

经营性应付项目调整额的增量约占总增量33.6%。因此，现金流增长不能全理解为回款能力增强，也不能据此认定异常拖欠。下一步应查应付项目构成与付款时点。

来源定位：本期半年报PDF p.118、上期半年报PDF p.126（印刷p.125）；公开案例隐去公司名称及代码。本例由Agent按Skill读附注，并补充专用计算脚本，不是所有公司一键内置的现金流归因功能。

## 验证范围与局限

- 计算与异常处理测试16项通过：单位、非正基数、百分点、缺数、口径冲突、重述及来源核验等。
- 已用真实两期财报跑通；匿名能源公司本轮15项可比，仅表示录入指标的覆盖范围。
- 金额支持元、万元、百万元、亿元。PDF文本匹配只证明数字存在，不能替代语义和表格列头核对。
- 不覆盖银行、保险等金融企业；扫描件、复杂合并或重述需要额外核实。
- 公司解释不等于独立验证的因果；没有分项证据就保留待查问题。
- 下期检查项目前是研究清单，未实现自动跨期继承与逐项核销。
- 文件由所选Agent处理，本地存放不等于不会传至模型服务。

## 维护者复现

在仓库根目录运行：

```bash
python3 scripts/validate_skills.py
python3 -m unittest discover -s tests -p test_financial_report_changes.py -v
```

在Skill目录运行：

```bash
python3 scripts/extract_pdf.py OLD.pdf NEW.pdf --output work/sources.json
# 文本乱码时重试：加 --engine pypdfium2
# 由Agent按references/input-schema.md填写work/input.json，不能跳过语义核验。
python3 scripts/compare.py work/input.json --sources work/sources.json --output work/result
```

本工具用于资料整理与学习研究，不构成投资建议。代码与指令遵循仓库Apache-2.0许可。
