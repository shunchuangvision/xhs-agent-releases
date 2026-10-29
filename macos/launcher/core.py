"""Local lifecycle and signed GitHub Releases updater for Windows and macOS."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

PROTOCOL = 1
APP_ID = 'xhs-agent'
MAX_ARCHIVE = 300 * 1024 * 1024
MAX_EXPANDED = 900 * 1024 * 1024
MANIFEST_NAME = 'update-manifest.json'
SIGNATURE_NAME = 'update-manifest.sig'
REPOSITORY = 'shunchuangvision/xhs-agent-releases'


def platform_id():
    if sys.platform == 'darwin':
        return 'macos-arm64' if platform.machine() == 'arm64' else 'macos-x64'
    return 'windows-x64'


def manifest_names():
    if platform_id() == 'windows-x64':
        return MANIFEST_NAME, SIGNATURE_NAME
    return 'update-manifest-' + platform_id() + '.json', 'update-manifest-' + platform_id() + '.sig'


class LauncherError(Exception):
    pass


class ServiceConflict(LauncherError):
    """The listener cannot be authenticated as this installation; never stop it."""


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}', value):
        raise LauncherError('版本号必须为三段数字，例如 3.2.0')
    return tuple(map(int, value.split('.')))


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8-sig'))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8', newline='\n') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def repository_name(value):
    value = value.strip().rstrip('/')
    if value.startswith('https://github.com/'):
        value = value[len('https://github.com/'):]
    value = value.removesuffix('.git')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value):
        raise LauncherError('请填写 GitHub 的 owner/repository 或仓库完整地址')
    return value


def config(root):
    result = {'repository': REPOSITORY, 'port': 8001}
    result.update(read_json(Path(root) / 'launcher-config.json', {}) or {})
    result['repository'] = repository_name(result['repository'])
    if type(result['port']) is not int or not 1024 <= result['port'] <= 65535:
        raise LauncherError('端口应为 1024–65535 之间的整数')
    return result


def current_version(root):
    info = read_json(Path(root) / 'version.json', {'version': '3.1.0', 'dependencies': ''})
    version_tuple(info['version'])
    return info


def activate_dependencies(root):
    root = Path(root)
    info = current_version(root)
    dep = info.get('dependencies', '')
    if dep:
        version_tuple(dep)
        path = root / 'launcher/deps' / dep
    else:
        path = root / '.venv312/Lib/site-packages'
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return path


def contained(root, relative):
    """Reject aliases, Windows special paths and junction escapes before file writes."""
    if not isinstance(relative, str) or '\\' in relative or ':' in relative or '\x00' in relative:
        raise LauncherError('更新包包含不安全路径')
    parts = relative.split('/')
    reserved = r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?'
    if any(not p or p in ('.', '..') or p.endswith(('.', ' ')) or re.fullmatch(reserved, p, re.I) for p in parts):
        raise LauncherError('更新包包含非法文件名')
    base = Path(root).resolve()
    path = base
    for part in parts:
        path = path / part
        if path.exists() or path.is_symlink():
            attributes = path.lstat()
            if path.is_symlink() or getattr(attributes, 'st_file_attributes', 0) & 0x400:
                raise LauncherError('更新路径包含链接或目录联接，已停止安装')
    if not path.resolve().is_relative_to(base):
        raise LauncherError('更新路径超出应用目录')
    return path


def managed_path(name):
    if name in ('version.json', 'README.md', '.env.example', 'XHS-Agent.exe', 'start.bat', 'start.ps1', 'setup.ps1', 'package_source.py', 'pytest.ini'):
        return True
    if name in ('backend/requirements.txt', 'frontend/package.json'):
        return True
    if name.startswith(('backend/app/', 'frontend/dist/', 'docs/', 'extension/', 'tools/')):
        return True
    if name in ('launcher/core.py', 'launcher/ui.py', 'launcher/server.py', 'launcher/__init__.py', 'launcher/bootstrap.cs'):
        return True
    return bool(re.fullmatch(r'launcher/deps/\d{1,4}\.\d{1,4}\.\d{1,4}/.+', name))


@contextmanager
def installation_lock(root):
    path = contained(root, '.launcher/install.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        handle.seek(0)
        handle.write(b'0')
        handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise LauncherError('另一个启动器正在启动、停止或更新，请稍后再试') from exc
        yield
    finally:
        handle.close()


def https_url(url):
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ''
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
            or not (host in ('github.com', 'api.github.com') or host.endswith('.githubusercontent.com'))):
        raise LauncherError('更新下载地址必须来自 GitHub 的 HTTPS 服务')
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit, destination=None, progress=lambda _: None):
    https_url(url)
    request = urllib.request.Request(url, headers={'User-Agent': 'XHS-Agent-Launcher/1', 'Accept': 'application/vnd.github+json' if 'api.github.com' in url else '*/*'})
    opener = urllib.request.build_opener(SafeRedirect)
    try:
        with opener.open(request, timeout=35) as response:
            length = int(response.headers.get('Content-Length') or 0)
            if length > limit:
                raise LauncherError('更新文件超过允许大小')
            chunks, received, last = [], 0, 0
            output = Path(destination).open('wb') if destination else None
            try:
                while chunk := response.read(256 * 1024):
                    received += len(chunk)
                    if received > limit:
                        raise LauncherError('更新文件超过允许大小')
                    output.write(chunk) if output else chunks.append(chunk)
                    if destination and time.monotonic() - last > .4:
                        progress('正在下载：%.1f MB%s' % (received / 1048576, ' / %.1f MB' % (length / 1048576) if length else ''))
                        last = time.monotonic()
                if length and length != received:
                    raise LauncherError('更新包下载不完整，请重新检查更新')
            finally:
                if output:
                    output.close()
            return b''.join(chunks) if not destination else received
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LauncherError('尚未发布正式更新，或更新仓库不可公开访问') from None
        if exc.code in (403, 429):
            raise LauncherError('GitHub 暂时限制请求，请稍后再试') from None
        raise LauncherError('GitHub 下载失败（HTTP %s）' % exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LauncherError('无法连接 GitHub，请检查网络后重试；当前版本未改动') from None


def verify_manifest(raw, signature_raw, public_key):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        signature = json.loads(signature_raw)
        key_data = Path(public_key).read_bytes()
        if signature['algorithm'] != 'Ed25519' or signature['key_id'] != hashlib.sha256(key_data).hexdigest():
            raise ValueError()
        key = load_pem_public_key(key_data)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError()
        key.verify(base64.b64decode(signature['signature'], validate=True), raw)
    except Exception as exc:
        raise LauncherError('更新包签名验证失败，已拒绝安装') from exc
    try:
        manifest = json.loads(raw)
        version_tuple(manifest['version'])
        target_platform = platform_id()
        if manifest['app_id'] != APP_ID or manifest['platform'] != target_platform or manifest['format'] != 1:
            raise ValueError()
        if manifest['min_launcher_protocol'] > PROTOCOL:
            raise LauncherError('此版本需要新版启动器，请下载完整安装包')
        if manifest['archive'] != f'xhs-agent-{manifest["version"]}-{target_platform}-update.zip':
            raise ValueError()
        if not re.fullmatch(r'[0-9a-f]{64}', manifest['sha256']) or not 0 < manifest['size'] <= MAX_ARCHIVE:
            raise ValueError()
        files = manifest['files']
        if not isinstance(files, dict) or not 1 <= len(files) <= 20000:
            raise ValueError()
        folded, total = set(), 0
        for name, info in files.items():
            contained(Path.cwd(), name)
            if not managed_path(name) or name.casefold() in folded:
                raise ValueError()
            folded.add(name.casefold())
            if type(info['size']) is not int or info['size'] < 0 or not re.fullmatch(r'[0-9a-f]{64}', info['sha256']):
                raise ValueError()
            if target_platform.startswith('macos-'):
                if name.startswith('launcher/deps/') and not name.startswith('launcher/deps/' + manifest['version'] + '/'):
                    raise ValueError()
                expected_mode = 0o755 if name == 'launcher/deps/' + manifest['version'] + '/xhs-runtime' else 0o644
                if info.get('mode') != expected_mode:
                    raise ValueError()
            total += info['size']
        if total > MAX_EXPANDED or not {'version.json', 'backend/app/main.py', 'frontend/dist/index.html'} <= files.keys():
            raise ValueError()
        if target_platform.startswith('macos-') and 'launcher/deps/' + manifest['version'] + '/xhs-runtime' not in files:
            raise ValueError()
        return manifest
    except LauncherError:
        raise
    except Exception as exc:
        raise LauncherError('更新清单格式错误或包含受保护路径') from exc


def check_update(root, progress=lambda _: None):
    root = Path(root)
    repository = config(root)['repository']
    progress('正在连接 GitHub，检查最新正式版本…')
    release = json.loads(fetch(f'https://api.github.com/repos/{repository}/releases/latest', 2 * 1024 * 1024))
    if release.get('draft') or release.get('prerelease'):
        raise LauncherError('当前通道仅接受正式版本')
    assets = {a['name']: a for a in release.get('assets', [])}
    manifest_name, signature_name = manifest_names()
    if not {manifest_name, signature_name} <= assets.keys():
        raise LauncherError('此 Release 未附带签名更新清单，请联系发布者')
    raw = fetch(assets[manifest_name]['browser_download_url'], 4 * 1024 * 1024)
    signature = fetch(assets[signature_name]['browser_download_url'], 8192)
    manifest = verify_manifest(raw, signature, root / 'launcher/release-public.pem')
    if release.get('tag_name', '').removeprefix('v') != manifest['version']:
        raise LauncherError('发布标签与签名版本不一致')
    asset = assets.get(manifest['archive'])
    if not asset or asset['size'] != manifest['size']:
        raise LauncherError('Release 缺少对应更新包或文件大小不一致')
    if asset.get('digest') and asset['digest'] != 'sha256:' + manifest['sha256']:
        raise LauncherError('GitHub 文件摘要与签名清单不一致')
    if version_tuple(manifest['version']) <= version_tuple(current_version(root)['version']):
        return None
    return {'manifest': manifest, 'raw': raw, 'signature': signature,
            'url': https_url(asset['browser_download_url']), 'notes': str(release.get('body') or '')[:16000]}


def extract_verified(archive, manifest, destination):
    if Path(archive).stat().st_size != manifest['size'] or sha256(archive) != manifest['sha256']:
        raise LauncherError('更新包完整性校验失败，已拒绝安装')
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) != len(manifest['files']) or {i.filename for i in entries} != manifest['files'].keys():
            raise LauncherError('更新压缩包文件列表与签名清单不一致')
        for item in entries:
            target = contained(destination, item.filename)
            expected = manifest['files'][item.filename]
            if (item.is_dir() or item.file_size != expected['size'] or item.flag_bits & 1
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise LauncherError('更新包包含不允许的文件类型')
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(item) as src, target.open('wb') as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            if sha256(target) != expected['sha256']:
                raise LauncherError('更新包内部文件校验失败')
            if os.name != 'nt':
                target.chmod(expected.get('mode', 0o644))
    info = read_json(Path(destination) / 'version.json')
    if info['version'] != manifest['version'] or info.get('dependencies') != manifest['version']:
        raise LauncherError('程序版本与依赖版本不匹配')


def control_token(root):
    path = contained(root, '.launcher/control.json')
    value = read_json(path)
    if not value:
        value = {'token': secrets.token_hex(32)}
        atomic_json(path, value)
    return value['token']


def control(root, action='status', timeout=4):
    port = config(root)['port']
    request = urllib.request.Request(f'http://127.0.0.1:{port}/_launcher/{action}',
        headers={'X-Launcher-Token': control_token(root)}, method='GET' if action == 'status' else 'POST',
        data=None if action == 'status' else b'')
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
            data = json.load(response)
        if data.get('root_id') != hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest():
            raise ServiceConflict('此端口属于另一份应用，未操作该服务')
        return data
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise LauncherError('当前仍有生成或运营任务，请等待完成后再停止或更新') from None
        if exc.code in (401, 403, 404):
            raise ServiceConflict('端口被旧启动方式或其他程序占用，请先关闭原服务，或在设置中换一个端口') from None
        raise LauncherError('本地服务暂时未能响应，请查看运行日志后重试') from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None


def process_alive(pid):
    if os.name != 'nt':
        try: os.kill(pid, 0); return True
        except ProcessLookupError: return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try: return kernel.WaitForSingleObject(handle, 0) == 258
    finally: kernel.CloseHandle(handle)


def python_path(root):
    if platform_id().startswith('macos-'):
        path = Path(root) / 'launcher/deps' / current_version(root)['dependencies'] / 'xhs-runtime'
        if path.is_file() and os.access(path, os.X_OK):
            return path
        raise LauncherError('缺少 Mac 运行环境，请重新下载完整安装包')
    for relative in ('launcher/runtime/python.exe',):
        path = Path(root) / relative
        if path.is_file():
            return path
    raise LauncherError('缺少运行环境，请下载并解压完整 Windows 包')


def start_service(root, maintenance=False, progress=lambda _: None):
    root = Path(root).resolve()
    existing = control(root)
    if existing:
        if existing.get('maintenance') and not maintenance:
            return control(root, 'activate', timeout=15)
        return existing
    port = config(root)['port']
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise LauncherError('端口已被其他程序占用，请关闭旧服务或修改端口')
    progress('正在启动本地服务…')
    log = contained(root, '.launcher/service.log')
    log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    if getattr(sys, 'frozen', False):
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    args = [str(python_path(root)), '-s', str(root / 'launcher/server.py'), '--port', str(port)]
    if maintenance:
        args.append('--maintenance')
    with log.open('ab') as stream:
        process = subprocess.Popen(args, cwd=root, env=env, stdout=stream, stderr=stream,
            start_new_session=os.name != 'nt',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LauncherError('服务启动失败，请查看启动器日志；用户配置和数据仍保留')
            state = control(root)
            if state:
                return state
            time.sleep(.3)
        raise LauncherError('启动等待超时，请查看日志后重试')
    except Exception:
        # Only terminate the exact child created by this invocation, never another listener.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        raise


def stop_service(root, progress=lambda _: None):
    state = control(root)
    if not state:
        # A control timeout must not masquerade as a stopped service.
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', config(root)['port'])) == 0:
                raise LauncherError('端口仍在使用，但服务未响应停止检查。启动器暂不退出，请稍后重试或查看运行日志')
        return False
    progress('正在等待服务安全停止…')
    control(root, 'stop')
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if not process_alive(state['pid']):
            return True
        time.sleep(.25)
    raise LauncherError('服务尚未完全退出，已停止更新。请等待当前操作完成后重试')


def database_path(root):
    activate_dependencies(root)
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    root = Path(root).resolve()
    values = dotenv_values(root / '.env')
    url = make_url(values.get('DATABASE_URL') or 'sqlite:///./data/xhs-agent.db')
    if url.get_backend_name() != 'sqlite':
        raise LauncherError('自动更新当前支持本地 SQLite 数据库；外部数据库请先备份并手工升级')
    path = Path(url.database)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def database_backup(source, target):
    if not Path(source).exists():
        return False
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(target) as dest:
        src.backup(dest)
    return True


def restore_transaction(root, transaction):
    root = Path(root).resolve()
    if control(root):
        stop_service(root)
    for entry in reversed(transaction['changes']):
        target = contained(root, entry['path'])
        if entry['before'] is None:
            target.unlink(missing_ok=True)
        else:
            backup = contained(root, transaction['backup'] + '/files/' + entry['path'])
            if sha256(backup) != entry['before']:
                raise LauncherError('备份校验失败，请保留现场并联系维护者')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
    saved_db = contained(root, transaction['backup'] + '/database.sqlite')
    if transaction.get('database_existed'):
        if sha256(saved_db) != transaction['database_sha256']:
            raise LauncherError('数据库备份校验失败，请联系维护者')
        with sqlite3.connect(saved_db) as src, sqlite3.connect(transaction['database']) as dest:
            src.backup(dest)
    elif Path(transaction['database']).exists():
        # A first launch may have created a DB. Keep it rather than deleting user data.
        pass
    atomic_json(root / '.launcher/installed.json', transaction['old_installed'])
    transaction['state'] = 'rolled_back'
    atomic_json(root / '.launcher/transaction.json', transaction)


def recover_interrupted(root, progress=lambda _: None):
    transaction = read_json(Path(root) / '.launcher/transaction.json')
    if transaction and transaction['state'] == 'applying':
        progress('检测到上次更新未完成，正在恢复备份…')
        restore_transaction(root, transaction)
        return True
    return False


def apply_package(root, archive, raw, signature, progress=lambda _: None):
    """Must be called with installation_lock. Never overwrites data/config/trust/runtime."""
    root = Path(root).resolve()
    recover_interrupted(root, progress)
    manifest = verify_manifest(raw, signature, root / 'launcher/release-public.pem')
    if version_tuple(manifest['version']) <= version_tuple(current_version(root)['version']):
        raise LauncherError('已安装相同或更新的版本，不执行降级')
    stage = contained(root, '.launcher/staging/' + uuid.uuid4().hex)
    stage.mkdir(parents=True)
    progress('正在校验并解压更新包…')
    extract_verified(archive, manifest, stage)
    before_state = control(root)
    if before_state and before_state['busy']:
        raise LauncherError('当前仍有任务运行，更新包已下载；请等任务完成后重试')
    dbpath = database_path(root)
    # Validate every target before stopping service or touching installed files.
    installed = read_json(root / '.launcher/installed.json', {'files': []})
    new_names = set(manifest['files'])
    old_names = {n for n in installed.get('files', []) if managed_path(n) and not n.startswith('launcher/deps/')}
    paths = sorted(new_names | old_names)
    for name in paths:
        path = contained(root, name)
        if path.exists() and not path.is_file():
            raise LauncherError('更新目标被同名目录占用：' + name)
    running = stop_service(root, progress) if before_state else False
    backup_relative = '.launcher/backups/' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    backup = contained(root, backup_relative)
    backup.mkdir(parents=True)
    changes = []
    transaction = None
    env_hash = sha256(root / '.env') if (root / '.env').exists() else None
    try:
        progress('正在备份当前程序和数据库…')
        existed = database_backup(dbpath, backup / 'database.sqlite')
        for name in paths:
            target = contained(root, name)
            before = sha256(target) if target.is_file() else None
            if name in new_names and before == manifest['files'][name]['sha256']:
                continue
            if before:
                saved = contained(backup / 'files', name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            changes.append({'path': name, 'before': before})
        transaction = {'state': 'applying', 'backup': backup_relative, 'changes': changes,
            'old_installed': installed, 'database': str(dbpath), 'database_existed': existed,
            'database_sha256': sha256(backup / 'database.sqlite') if existed else None,
            'version': manifest['version'], 'previously_running': running}
        atomic_json(root / '.launcher/transaction.json', transaction)
        progress('正在安装 ' + manifest['version'] + '…')
        # Activation metadata is last. The process doing the update uses immutable bootstrap runtime.
        for entry in sorted(changes, key=lambda e: e['path'] == 'version.json'):
            name = entry['path']; target = contained(root, name)
            if name in new_names:
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = contained(root, name + '.' + uuid.uuid4().hex + '.installing')
                shutil.copy2(contained(stage, name), temporary)
                os.replace(temporary, target)
            else:
                target.unlink(missing_ok=True)
        assert env_hash == (sha256(root / '.env') if (root / '.env').exists() else None), 'Configuration changed'
        progress('正在检查新版本能否启动…')
        state = start_service(root, maintenance=True, progress=progress)
        if state['version'] != manifest['version']:
            raise LauncherError('新版本启动校验不通过')
        if not running:
            stop_service(root, progress)
        atomic_json(root / '.launcher/installed.json', {'version': manifest['version'], 'files': sorted(new_names)})
        transaction['state'] = 'complete'
        atomic_json(root / '.launcher/transaction.json', transaction)
        if running:
            control(root, 'activate', timeout=15)
        progress('更新完成，用户配置、文案和素材已保留')
        return manifest['version']
    except Exception as exc:
        if transaction and transaction['state'] == 'complete':
            raise LauncherError('程序已更新，服务激活未完成；请点击启动系统。数据不会回退') from exc
        if transaction:
            progress('更新未完成，正在恢复上一版本…')
            restore_transaction(root, transaction)
        if running:
            start_service(root, progress=progress)
        if isinstance(exc, LauncherError):
            raise LauncherError(str(exc) + '；已恢复上一版本') from exc
        raise LauncherError('更新失败，已恢复上一版本。请查看启动器日志') from exc


def install_update(root, update, progress=lambda _: None):
    with installation_lock(root):
        recover_interrupted(root, progress)
        archive = contained(root, '.launcher/downloads/' + update['manifest']['archive'])
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists() or sha256(archive) != update['manifest']['sha256']:
            temporary = archive.with_suffix('.download')
            try:
                fetch(update['url'], MAX_ARCHIVE, temporary, progress)
                if sha256(temporary) != update['manifest']['sha256']:
                    raise LauncherError('下载文件校验失败，请重试')
                os.replace(temporary, archive)
            finally:
                temporary.unlink(missing_ok=True)
        return apply_package(root, archive, update['raw'], update['signature'], progress)
