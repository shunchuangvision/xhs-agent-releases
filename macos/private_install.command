#!/bin/bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
source_app="$bundle_dir/XHS-Agent.app"
payload_dir="$source_app/Contents/Resources/payload"
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$source_app/Contents/Info.plist")
runtime="$payload_dir/launcher/deps/$version/xhs-runtime"
target_dir="${XHS_PRIVATE_APPLICATIONS_DIR:-$HOME/Applications}"
target_app="$target_dir/XHS-Agent.app"

trap 'printf "\n安装未完成。请查看上方提示，关闭正在执行的任务后重试。\n"' ERR
/usr/bin/codesign --verify --deep --strict "$source_app"
if [[ ! -x "$runtime" || ! -f "$bundle_dir/private-model.env" ]]; then
  printf '私发包不完整，请重新完整解压。\n' >&2
  exit 1
fi
mkdir -p "$target_dir"
if [[ ! -e "$target_app" ]]; then
  temporary_dir=$(mktemp -d "$target_dir/.xhs-private-install.XXXXXX")
  /usr/bin/ditto "$source_app" "$temporary_dir/XHS-Agent.app"
  /bin/mv "$temporary_dir/XHS-Agent.app" "$target_app"
  /bin/rmdir "$temporary_dir"
elif [[ ! -d "$target_app/Contents" ]]; then
  printf '目标位置已存在同名文件，请先检查用户应用程序目录。\n' >&2
  exit 1
fi
"$runtime" -s "$bundle_dir/private_setup.py" --app "$source_app" --config "$bundle_dir/private-model.env"
printf '\n配置完成。之后可直接打开用户应用程序目录中的 XHS-Agent。\n'
if [[ "${1:-}" != '--no-open' ]]; then
  /usr/bin/open "$target_app"
fi
