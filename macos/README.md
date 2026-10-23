# XHS Agent · Mac 版

适用于 macOS 14 或以上。Apple 芯片（M1/M2/M3/M4 等）选择 `macos-arm64`；Intel 选择 `macos-x64`。

1. 打开 DMG，把 XHS-Agent.app 拖入 Applications（应用程序）。也可以完整解压 ZIP 后移动应用。
2. 打开 XHS-Agent，点击“启动并打开系统”。首次启动需要准备运行环境，请稍等。
3. 从 v3.2.1 起，网页首次打开会先要求填写接口地址、模型名称和自己的 API Key。保存时验证连接，通过后进入工作台；已有配置直接进入，重启和更新无需重新配置。需要更换时打开“模型与访问设置”，同一接口下密钥留空可保留原值。测试和保存会发出一次简短模型请求，可能产生少量费用。
4. 后续点击启动器的“检查更新 → 下载并安装”。更新完成后启动器重新打开。

安装包自带 Python 和依赖，无需安装 Python、Node.js 或 Git。

当前安装包采用本地临时签名，尚未经过 Apple 开发者签名和公证。如果 macOS 拦截已确认来源的下载，请先尝试打开一次，再到“系统设置 → 隐私与安全性”点击“仍要打开”。不要关闭系统安全检查。企业设备如受策略限制，请由管理员处理。操作依据：[Apple 官方说明](https://support.apple.com/zh-cn/102445)。

程序、模型设置、文案数据库、素材和日志保存在：
`~/Library/Application Support/XHS-Agent`

其中 `.env` 保存配置，`data/` 保存文案与素材，`.launcher/` 保存启动和更新日志。更换 .app 或应用更新不会覆盖这些个人文件。删除 .app 也不会删除文案数据。如需迁移，请先停止服务并备份整个数据目录。

首次安装会复制程序到数据目录；以后打开 .app 会启动已安装的最新版本，不会用旧安装包覆盖更新。安装新功能使用启动器的更新入口。关闭启动器后服务继续运行；退出系统服务请点击“停止服务”。

更新期间如果有生成任务，会拒绝安装并提示稍后重试。更新包校验签名和哈希，安装前备份程序及 SQLite 数据库，启动校验失败时恢复备份。外部 PostgreSQL 数据库仍需管理员手工备份升级。

## 维护者

GitHub Actions 的 `Build macOS` 工作流在 Apple 芯片和 Intel Mac 上分别构建。基础业务代码来自对应 Windows Release 的签名更新包，`macos/launcher` 提供共用启动器的 Mac 适配层。后续版本先创建预发布（Pre-release），附带同版本 Windows 的签名更新资产，再运行 Mac 构建；三个平台资产与签名齐全后，才把版本设为正式发布和 Latest，以免现有启动器检查到不完整的发布。

工作流只产生待签名构建产物，签名私钥不上传 GitHub。下载 Actions 产物后，在发布电脑执行 `macos/sign_release.py`，再运行 `gh release upload` 上传 Mac 的 DMG、ZIP、更新 ZIP、对应 JSON 和 SIG 文件。也可运行 `Publish tested macOS artifacts`，把通过验收的安装包从 Actions 直接上传到预发布；发布电脑核验其清单与 GitHub 文件摘要后本地签名，只上传 SIG 文件。保留已有 Windows 的 `update-manifest.json` 和 `update-manifest.sig`。

转为正式发布后运行 `Verify published macOS updates`，确认两个芯片版本均能从公开 GitHub 地址检查、下载、验签并完成升级。该验收仅使用临时目录及演示数据。

`ci_test.py` 使用一次性测试签名密钥和演示数据验证应用启动、文案生成、Worker、签名升级、数据保留和升级后原生窗口。正式发布密钥不会用于测试。
