# XHS-Agent Windows 下载与更新

XHS-Agent 是本地运行的小红书内容创作与运营工作台。此仓库仅分发程序发布包和更新说明，不保存用户文案、图片、模型密钥或数据库。

## 下载使用

进入 [最新版本](https://github.com/shunchuangvision/xhs-agent-releases/releases/latest)，下载名称以 `windows-x64-portable.zip` 结尾的完整包。

完整解压后双击 `XHS-Agent.exe`，点击 **启动并打开系统**。运行环境已包含，无需另外安装 Python、Node.js 或 Git。

首次可将 `.env.example` 复制为 `.env`，填写自己的模型配置。没有模型配置时为演示模式。

## 后续更新

启动器 → **检查更新** → **下载并安装**。

更新器验证 Ed25519 签名及文件 SHA-256，备份后更新程序，保留本地配置、文案和素材。服务有任务运行时会提示稍后更新。更新失败时恢复备份。

`update.zip` 是启动器使用的增量安装入口，不能代替首次完整安装包。旧版用户第一次需要完整解压新版启动器，之后即可通过按钮更新。

适用平台：Windows x64。自动备份与更新面向本地 SQLite 数据库。

## 文件说明

- `*-portable.zip`：首次安装与分发使用，包含启动器和运行环境。
- `*-update.zip`：启动器下载的程序更新包。
- `update-manifest.json` / `update-manifest.sig`：更新清单和发布签名。
- `SHA256SUMS.txt`：发布附件摘要。

请勿将 `.env`、`data`、`.launcher` 或发布私钥上传到此公开仓库。
