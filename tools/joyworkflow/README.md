# JoyWorkflow · 本地策略研究工具

从游资五层框架出发，选择或自定义量化研究条件，自动获取公开行情，在本地完成校验、筛选、可视化报告与历史留档。**规则代码计算，本轮不调用 AI 模型，不自动交易。**

## 下载即用

**[下载 Mac Apple Silicon 应用包（0.5.0）](https://github.com/havejoyy/flowai-finance-lab/releases/download/joyworkflow-v0.5.0/JoyWorkflow-macOS-AppleSilicon-0.5.0.zip)** · [版本说明与校验文件](https://github.com/havejoyy/flowai-finance-lab/releases/tag/joyworkflow-v0.5.0) · [完整使用说明](docs/桌面应用使用说明.md)

适用 macOS 13+、Apple Silicon（M系列）；内置 Python 与 SQLite，无需安装 Python、Node、数据库，也不需要准备数据或行情 API Key。

当前为 **本地预览版**：ad-hoc 签名，未完成 Developer ID 签名和 Apple 公证；只在开发者的 arm64 Mac 验证，未验证另一台 Mac 首开、Intel Mac 或 Windows。系统拦截时请等待后续签名版本，不要求关闭安全校验。

## 四步使用

1. 解压，双击 `JoyWorkflow.app`。也可先拖到“应用程序”。
2. 选择“放量走强”“弱转强观察”“板块共振”或“游资五层共振·放量突破”；需要修改时进入“我的策略”。
3. 在五层指标库添加指标、调整阈值、保存版本，点击“获取数据并运行筛选”。
4. 在“历史筛选”阅读入选股票的实际值与阈值，并查看未核验范围、缺失原因、日期和数据来源；可导出完整记录。

“本地数据”按资料分类展示 SQLite 表格。相同策略版本已运行时，可回看旧报告或重新执行。

## 五层框架如何落地

| 层级 | 已实现的量化观察 |
|---|---|
| 市场环境 | 上涨占比、跌停占比、炸板率、昨日涨停反馈 |
| 行业共振 | 行业强度排名、上涨范围、涨停数量、相对基准表现及持续性 |
| 活跃代表 | 成交额、行业内成交额排名、相对行业强弱和趋势 |
| 价格结构 | 突破、前五日成交额倍数、收盘位置等 |
| 风险约束 | 流动性、ATR、均线乖离、公告目录核验等 |

指标、口径与默认门槛见 [五层指标库](docs/五层指标库.md) 和 [当前量化规则](docs/当前量化规则.md)。自定义策略只将已选指标用于判定，固定数据完整性校验继续执行。行情指标是研究代理，不代表识别了真实游资、主线或龙头；个人交易计划与纪律在无输入时标为不适用。

## 自动取数与缺失处理

按需从新浪、腾讯、交易所、东方财富与申万等公开接口获取数据；先校验本地缓存，再补取过期或缺失资料。适用时增量更新历史，发现口径修订时重新获取。原始证据、日期和引用随本地批次保存。

断线、超时和服务端暂时异常执行有限重试；日期、结构和证书失败明确显示原因。缺失不能按零值、旧日或示例补齐。“重试缺失数据”新建关联任务，保留原报告。公开接口可用性不受本项目控制，网络恢复不意味着缺失一定消失。

默认数据目录：`~/Library/Application Support/JoyWorkflow/`；可用原生菜单切换到已有 SQLite 所在文件夹。资料不随更新应用覆盖，也不上传到作者服务器。运行筛选需要联网，关闭应用停止取数，无每日后台更新承诺。

## 已完成验证

2026-10-31 在打包应用中用 **2026-10-30** 真实行情完成流程：4,984 只有效证券，初筛135只，按验收配置核验10只，2只满足全部条件、8只不符；另125只未核验、5只合资格行情缺口单列。51项五层资料复用49项、联网补齐2项，个股历史复用10只。

这次使用独立 SQLite 备份验证缓存路径；不是空数据首次全量下载验收。状态为 `PARTIAL`，不代表全市场完整筛选。规则独立复算、85项测试、移动路径自检和关闭后重启回看通过。仅是功能验证，未验证收益、长期取数稳定性或其他平台。

## 从源码运行

```bash
cd tools/joyworkflow
python3 -m unittest discover -p 'test_*.py' -v
python3 app.py --data-dir ./data
```

源码要求 Python 3.9+，标准库即可运行研究页面（桌面构建另需打包依赖）。终端会显示本机页面地址；这是开发者路径，普通 Mac 用户使用上面的应用包。

在 Apple Silicon Mac 上构建桌面包：

```bash
python3.12 -m venv .desktop-build-venv
.desktop-build-venv/bin/python -m pip install -r desktop/build-requirements.lock
.desktop-build-venv/bin/python build_desktop.py
```

需要 Xcode Command Line Tools（Swift、codesign 等）。构建过程校验 Skill 资源、公开中间证书和冻结程序 SSL 环境，输出 `dist/desktop/`。此脚本不会进行 Developer ID 签名或公证。

[来源说明](NOTICE.md) · [数据与研究边界](../../DISCLAIMER.md)。软件授权不额外授予行情数据许可。Agent Skill 的安装和本地桌面工具是不同入口，`npx skills add` 不会安装本应用。
