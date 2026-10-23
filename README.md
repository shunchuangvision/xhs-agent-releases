# XHS-Agent 下载与更新

XHS-Agent 是本地运行的小红书内容创作与运营工作台。此仓库提供程序发布包、更新说明及 Mac 启动器构建脚本，不保存用户文案、图片、模型密钥或数据库。

## 下载使用

进入 [最新版本](https://github.com/shunchuangvision/xhs-agent-releases/releases/latest)，下载名称以 `windows-x64-portable.zip` 结尾的完整包。

完整解压后双击 `XHS-Agent.exe`，点击 **启动并打开系统**。运行环境已包含，无需另外安装 Python、Node.js 或 Git。

从 v3.2.1 起，首次打开网页会先显示 API 配置页：填写服务商的接口地址、模型名称和自己的 API Key，保存并验证连接后进入工作台。已有配置直接进入工作台，重启和更新不用重新填写。以后可在“模型与访问设置”修改。

Mac 用户下载 `macos-arm64.dmg`（Apple 芯片）或 `macos-x64.dmg`（Intel），打开后拖入“应用程序”，双击 XHS-Agent。也提供 ZIP 安装包。无需另装运行环境，详见 [Mac 使用说明](macos/README.md)。

## 后续更新

启动器 → **检查更新** → **下载并安装**。

v3.2.1 已分别发布三个平台的更新包，启动器会自动匹配系统和芯片：

| 平台 | 更新包 |
| --- | --- |
| Windows x64 | [Windows 更新 ZIP](https://github.com/shunchuangvision/xhs-agent-releases/releases/download/v3.2.1/xhs-agent-3.2.1-windows-x64-update.zip) |
| Mac Apple 芯片（M 系列） | [macOS arm64 更新 ZIP](https://github.com/shunchuangvision/xhs-agent-releases/releases/download/v3.2.1/xhs-agent-3.2.1-macos-arm64-update.zip) |
| Mac Intel | [macOS x64 更新 ZIP](https://github.com/shunchuangvision/xhs-agent-releases/releases/download/v3.2.1/xhs-agent-3.2.1-macos-x64-update.zip) |

请通过启动器安装更新；单独下载 ZIP 不会自动安装，也不建议手工覆盖程序目录。三个平台分别带有签名清单和签名，发布在同一版本下以兼容已分发启动器的更新检查。

更新器验证 Ed25519 签名及文件 SHA-256，备份后更新程序，保留本地配置、文案和素材。服务有任务运行时会提示稍后更新。更新失败时恢复备份。

`update.zip` 是启动器使用的程序更新包，不能代替首次完整安装包。旧版用户第一次需要完整解压新版启动器，之后即可通过按钮更新。

适用平台：Windows x64、macOS 14 或以上的 Apple 芯片与 Intel Mac。自动备份与更新面向本地 SQLite 数据库。

## 文件说明

- `*-portable.zip`：首次安装与分发使用，包含启动器和运行环境。
- `*-update.zip`：启动器下载的程序更新包。
- `update-manifest.json` / `update-manifest.sig`：更新清单和发布签名。
- `update-manifest-macos-*.json` / `.sig`：按芯片区分的 Mac 更新清单和签名。
- `SHA256SUMS.txt`：发布附件摘要。

请勿将 `.env`、`data`、`.launcher` 或发布私钥上传到此公开仓库。
