# 来源与第三方说明

本工具在作者维护的 microApp / JoyWorkflow 0.1.0 基础上新增独立游资研究页面、五层量化计算、本地数据管理与桌面启动器；不包含该原项目的数据库或凭据。

随包 `skills/youzi-market-research/` 保存独立固定版本，通过 manifest 校验其资源，作为框架与手动资料检查的依据。桌面规则计算器使用显式量化规则，不代表真实模型调用，也不复制某位投资者的真实策略。

自有代码沿用本仓库 Apache-2.0；第三方组件保持各自许可证：CPython（PSF）、PyInstaller（GPL及bootloader exception）、certifi CA集合（MPL-2.0）。Apple AppKit/WebKit 为系统框架。公开中间证书来源与验证方式见 `desktop/certificates/README.md`。

软件许可不包含行情供应商的数据授权。用户运行时按需请求公开接口；本源码仓库与安装包不分发用户行情缓存、个人策略、模型凭据或实际交易记录。examples 中固定虚构样例只用于流程测试。
