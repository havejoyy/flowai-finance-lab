# JoyWorkflow · 本地策略研究工具

从游资五层框架出发，选择或自定义量化研究条件，自动获取公开行情，在本地完成校验、筛选、可视化报告与历史留档。**规则代码计算，本轮不调用 AI 模型，不自动交易。**

## 下载即用

| 平台 | 下载 | 启动 |
|---|---|---|
| Windows x64 0.5.1 | [便携版 ZIP](https://github.com/havejoyy/flowai-finance-lab/releases/download/joyworkflow-v0.5.1/JoyWorkflow-Windows-x64-0.5.1.zip) | 完整解压，双击 `JoyWorkflow.exe` |
| Mac Apple Silicon 0.5.0 | [应用 ZIP](https://github.com/havejoyy/flowai-finance-lab/releases/download/joyworkflow-v0.5.0/JoyWorkflow-macOS-AppleSilicon-0.5.0.zip) | 解压，双击 `JoyWorkflow.app` |

两种应用均内置 Python 和 SQLite，不需安装 Python、Node、数据库，也不需要自己准备行情或 API Key。[Windows版本页](https://github.com/havejoyy/flowai-finance-lab/releases/tag/joyworkflow-v0.5.1) · [桌面使用说明](docs/桌面应用使用说明.md) · [手机端方案](docs/手机端方案.md)。

Windows便携版启动后自动在默认浏览器打开本机研究页面，启动器提供重新打开、数据文件夹、日志和退出入口。必须保留 `_internal` 文件夹；不要直接在ZIP里运行，也不要只复制exe。关闭网页不会停止服务，退出启动器才会停止。

Windows目标平台为Windows 10/11 x64，自动化验收使用GitHub Windows x64 runner；普通用户机器的人工首开尚未验证。Mac要求macOS 13+ / M系列。当前均为预览版：Windows未进行Authenticode签名，Mac尚未Developer ID签名和公证；不能保证所有机器首开无系统提示，不要求关闭安全校验。Windows ARM、Intel Mac、原生手机App暂无安装包。

## 四步使用

1. 启动对应平台应用，等待本地页面就绪。
2. 选择“放量走强”“弱转强观察”“板块共振”或“游资五层共振·放量突破”；需要修改时进入“我的策略”。
3. 在五层指标库添加指标、调整阈值、保存版本，点击“获取数据并运行筛选”。
4. 在“历史筛选”阅读入选股票的实际值与阈值，并查看未核验范围、缺失原因、日期和数据来源；可导出完整记录。

“本地数据”按资料分类展示SQLite表格。相同策略版本已运行时，可回看旧报告或重新执行。

当前源码中的报告现将来源证券数、基础规则排除数、同日有效证券数、存在数据缺口的股票数和最终入选数分开展示；缺口表按股票代码合并列出具体原因。初筛候选、已核验及未核验数量另列，公共资料缺口不计成股票数量。历史报告按原记录生成同口径展示，不改写原始报告；有效证券数不表示已完整筛选全市场。本项改动尚未包含在上述既有桌面下载包中。

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

当前源码新增“本地数据 → 获取最新数据”：主动联网更新收盘快照与行业分类，校验后存入 SQLite，页面显示实际行情日期、进度和缺失原因。更新期间防止重复提交，刷新页面可继续查看任务；关闭服务后未完成任务标记为中断。个股历史日 K、指数等策略资料仍在运行筛选时按需补齐。本按钮尚未包含在上述已发布的桌面下载包中。

完整架构图、数据源对应表、缓存与 SQLite 流程见 [工具架构与数据获取](docs/工具架构与数据获取.md)。压缩包内置取数程序与运行环境，行情数据库在使用时自动建立；用户点击筛选时由自己的电脑直接请求公开数据服务。

按需从新浪、腾讯、交易所、东方财富与申万等公开接口获取数据；先校验本地缓存，再补取过期或缺失资料。适用时增量更新历史，发现口径修订时重新获取。原始证据、日期和引用随本地批次保存。

断线、超时和服务端暂时异常执行有限重试；日期、结构和证书失败明确显示原因。缺失不能按零值、旧日或示例补齐。“重试缺失数据”新建关联任务，保留原报告。公开接口可用性不受本项目控制，网络恢复不意味着缺失一定消失。

默认数据目录：Windows为 `%LOCALAPPDATA%\JoyWorkflow`，Mac为 `~/Library/Application Support/JoyWorkflow/`。Mac可用原生菜单切换SQLite目录；Windows启动器也支持 `--data-dir` 指定目录。资料不随更新应用覆盖，也不上传到作者服务器。运行筛选需要联网，关闭应用停止取数，无每日后台更新承诺。

## 验证范围

2026-10-31 在打包应用中用 **2026-10-30** 真实行情完成流程：4,984 只有效证券，初筛135只，按验收配置核验10只，2只满足全部条件、8只不符；另125只未核验、5只合资格行情缺口单列。51项五层资料复用49项、联网补齐2项，个股历史复用10只。

这次使用独立 SQLite 备份验证缓存路径；不是空数据首次全量下载验收。状态为 `PARTIAL`，不代表全市场完整筛选。规则独立复算、85项测试、移动路径自检和关闭后重启回看通过。仅是功能验证，未验证收益、长期取数稳定性或其他平台。

## 从源码运行

```bash
cd tools/joyworkflow
python3 -m unittest discover -p 'test_*.py' -v
python3 app.py --data-dir ./data
```

源码要求 Python 3.9+，标准库即可运行研究页面（桌面构建另需打包依赖）。终端会显示本机页面地址；这是开发者路径，普通 Mac 用户使用上面的应用包。

在Windows x64开发环境构建（终端需Python 3.12、OpenSSL；用户端无需）：

```powershell
python -m pip install -r desktop/windows-build-requirements.txt
python build_windows.py
python desktop/verify_windows.py
```

在 Apple Silicon Mac 上构建桌面包：

```bash
python3.12 -m venv .desktop-build-venv
.desktop-build-venv/bin/python -m pip install -r desktop/build-requirements.lock
.desktop-build-venv/bin/python build_desktop.py
```

需要 Xcode Command Line Tools（Swift、codesign 等）。构建过程校验 Skill 资源、公开中间证书和冻结程序 SSL 环境，输出 `dist/desktop/`。此脚本不会进行 Developer ID 签名或公证。

[来源说明](NOTICE.md) · [数据与研究边界](../../DISCLAIMER.md)。软件授权不额外授予行情数据许可。Agent Skill 的安装和本地桌面工具是不同入口，`npx skills add` 不会安装本应用。
