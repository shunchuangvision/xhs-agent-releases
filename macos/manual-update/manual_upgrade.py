"""Install the existing signed Mac release without querying the GitHub REST API."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

VERSION = '3.3.3'
ORIGIN = 'https://github.com/shunchuangvision/xhs-agent-releases/releases/download/v' + VERSION + '/'


class UpgradeError(Exception):
    pass


def download(url, target, limit):
    if not url.startswith(ORIGIN):
        raise UpgradeError('更新地址不属于指定的官方发行版本')
    print('下载：' + target.name, flush=True)
    result = subprocess.run([
        '/usr/bin/curl', '-q', '--silent', '--show-error', '--fail', '--location',
        '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15',
        '--max-time', '240', '--max-filesize', str(limit),
        '--output', str(target), '--write-out', '%{http_code}', url,
    ], capture_output=True, text=True, timeout=250)
    if result.returncode:
        status = result.stdout.strip()
        raise UpgradeError('下载未完成（HTTP ' + (status or '未知') + '）：' +
                           result.stderr.strip()[:600] + '\n尚未开始安装，请保留现有程序。')
    if not target.is_file() or target.stat().st_size > limit:
        raise UpgradeError('下载文件缺失或超过大小限制')


def require_closed_launcher(root):
    result = subprocess.run(['/bin/ps', '-axo', 'command='], capture_output=True,
                            text=True, check=True, timeout=10)
    marker = str(root / 'launcher/ui.py')
    if any(marker in line for line in result.stdout.splitlines()):
        raise UpgradeError('请先关闭 XHS Agent 启动器窗口，再重新运行此工具。不要强制结束运行中的任务。')


def upgrade(root, core, fetch=download, check_closed=require_closed_launcher):
    root = Path(root).resolve()
    target = core.platform_id()
    info = core.current_version(root)
    if target not in ('macos-arm64', 'macos-x64') or info.get('platform') != target:
        raise UpgradeError('此工具只支持与当前运行环境一致的 Mac 安装，请使用对应芯片的程序。')
    if core.version_tuple(info['version']) >= core.version_tuple(VERSION):
        print('已安装 ' + info['version'] + '，无需安装此更新。', flush=True)
        return 'already_current'
    check_closed(root)
    state = core.control(root)
    if state and state.get('busy'):
        raise UpgradeError('当前仍有任务运行，请等任务完成后重试。')
    print('准备升级：' + info['version'] + ' → ' + VERSION + '（' + target + '）', flush=True)
    print('使用官方签名更新包；原有模型配置、文案和素材保留。', flush=True)
    with tempfile.TemporaryDirectory(prefix='xhs-signed-update-') as directory:
        folder = Path(directory)
        manifest_file = folder / ('update-manifest-' + target + '.json')
        signature_file = manifest_file.with_suffix('.sig')
        fetch(ORIGIN + manifest_file.name, manifest_file, 4 * 1024 * 1024)
        fetch(ORIGIN + signature_file.name, signature_file, 8192)
        raw, signature = manifest_file.read_bytes(), signature_file.read_bytes()
        manifest = core.verify_manifest(raw, signature, root / 'launcher/release-public.pem')
        if manifest['version'] != VERSION or manifest['platform'] != target:
            raise UpgradeError('签名清单不是目标版本或芯片，已停止。')
        archive = folder / manifest['archive']
        fetch(ORIGIN + archive.name, archive, manifest['size'])
        if archive.stat().st_size != manifest['size'] or core.sha256(archive) != manifest['sha256']:
            raise UpgradeError('更新包大小或 SHA-256 不匹配，已拒绝安装。')
        with core.installation_lock(root):
            check_closed(root)
            result = core.apply_package(root, archive, raw, signature,
                                        lambda text: print(text, flush=True))
        if result != VERSION:
            raise UpgradeError('安装返回的版本不符合预期，请保留日志并联系维护者。')
    print('\n已更新至 ' + VERSION + '。请重新打开原来的 XHS-Agent.app。', flush=True)
    print('程序和数据库备份由原更新器保存在数据目录的 .launcher/backups 下。', flush=True)
    print('新版启动器直接读取 Release 签名清单，不再使用匿名 API 查询。系统代理设置保持不变。', flush=True)
    return 'updated'


def main():
    if sys.platform != 'darwin':
        raise UpgradeError('请在出问题的 Mac 上运行此工具。')
    root = Path(os.environ.get('XHS_AGENT_DATA_HOME') or
                str(Path.home() / 'Library/Application Support/XHS-Agent')).expanduser().resolve()
    if not (root / 'launcher/core.py').is_file():
        raise UpgradeError('未找到已有 XHS Agent 数据目录，此工具不用于首次安装。')
    sys.path.insert(0, str(root / 'launcher'))
    os.chdir(root)
    core = importlib.import_module('core')
    core.activate_dependencies(root)
    upgrade(root, core)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n操作已中断。如在安装过程中中断，请先用原启动器恢复未完成的更新。', file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print('\n升级未完成：' + str(exc), file=sys.stderr)
        sys.exit(1)
