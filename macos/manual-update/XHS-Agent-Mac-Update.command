#!/bin/bash
set -eu

xhs_tool_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
xhs_root="${XHS_AGENT_DATA_HOME:-$HOME/Library/Application Support/XHS-Agent}"
echo 'XHS Agent · Mac 3.3.3 签名手动升级'
echo '请先等待任务完成并关闭启动器。此工具不会查询 GitHub API。'

if [ ! -f "$xhs_root/version.json" ]; then
    echo '未找到已安装的 XHS Agent。请在出问题的 Mac 用户账户下运行。'
    read -r -p '按回车结束…' xhs_unused
    exit 1
fi
xhs_dep="$(/usr/bin/plutil -extract dependencies raw -o - "$xhs_root/version.json")"
if [[ ! "$xhs_dep" =~ ^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$ ]]; then
    echo '本机版本信息异常，未执行任何安装。'
    exit 1
fi
xhs_runtime="$xhs_root/launcher/deps/$xhs_dep/xhs-runtime"
if [ ! -x "$xhs_runtime" ] || [ ! -f "$xhs_tool_dir/manual_upgrade.py" ]; then
    echo '未找到完整的运行环境或升级脚本。请先完整解压本工具。'
    exit 1
fi

xhs_result=0
PYINSTALLER_RESET_ENVIRONMENT=1 "$xhs_runtime" -s "$xhs_tool_dir/manual_upgrade.py" || xhs_result=$?
read -r -p '按回车结束…' xhs_unused || true
exit "$xhs_result"
