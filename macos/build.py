"""Build on a native Mac. Publisher signs manifests locally, outside GitHub Actions."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'launcher'))
import core


def run(args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def prepare_source(output, base_version, version):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    core.version_tuple(base_version); core.version_tuple(version)
    prefix = f'https://github.com/{core.REPOSITORY}/releases/download/v{base_version}/'
    raw = urllib.request.urlopen(prefix + 'update-manifest.json', timeout=60).read()
    signature = json.load(urllib.request.urlopen(prefix + 'update-manifest.sig', timeout=60))
    key = (HERE / 'launcher/release-public.pem').read_bytes()
    assert signature['algorithm'] == 'Ed25519' and signature['key_id'] == hashlib.sha256(key).hexdigest()
    load_pem_public_key(key).verify(base64.b64decode(signature['signature'], validate=True), raw)
    manifest = json.loads(raw)
    assert manifest['app_id'] == core.APP_ID and manifest['platform'] == 'windows-x64'
    assert manifest['version'] == base_version and manifest['archive'] == f'xhs-agent-{base_version}-windows-x64-update.zip'
    download = output / 'source-windows.zip'
    with urllib.request.urlopen(prefix + manifest['archive'], timeout=120) as source, download.open('wb') as dest:
        shutil.copyfileobj(source, dest)
    assert download.stat().st_size == manifest['size'] and core.sha256(download) == manifest['sha256']
    payload = output / 'payload'
    payload.mkdir()
    with zipfile.ZipFile(download) as archive:
        for name, info in manifest['files'].items():
            if not (name.startswith(('backend/app/', 'frontend/dist/', 'docs/', 'extension/')) or name in ('.env.example', 'backend/requirements.txt', 'README.md')):
                continue
            target = core.contained(payload, name)
            data = archive.read(name)
            assert len(data) == info['size'] and hashlib.sha256(data).hexdigest() == info['sha256']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    shutil.copytree(HERE / 'launcher', payload / 'launcher', ignore=shutil.ignore_patterns('__pycache__'))
    core.atomic_json(payload / 'version.json', {'version': version, 'dependencies': version,
        'launcher_protocol': core.PROTOCOL, 'platform': core.platform_id()})
    shutil.copy2(HERE / 'README.md', payload / 'docs/macos-guide.md')
    return payload


def build(args):
    assert sys.platform == 'darwin', 'Run this builder on macOS.'
    output = args.output.resolve(); output.mkdir(parents=True)
    payload = prepare_source(output, args.base_version, args.version)
    # Dependency versions match the base app. pytest is a build-only dependency.
    requirements = [x for x in (payload / 'backend/requirements.txt').read_text().splitlines() if not x.startswith('pytest')]
    run([sys.executable, '-m', 'pip', 'install', *requirements, 'cryptography>=44,<47', 'certifi', 'pyinstaller==6.16.0'])
    package_names = ['fastapi', 'uvicorn', 'sqlalchemy', 'pydantic', 'httpx', 'anyio', 'dotenv',
                     'openpyxl', 'PIL', 'pypdf', 'cryptography', 'psycopg', 'psycopg_binary', 'certifi']
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
           '--name', 'xhs-runtime', '--distpath', output / 'frozen', '--workpath', output / 'pyinstaller',
           '--specpath', output, '--paths', HERE, '--exclude-module', 'pytest', '--osx-bundle-identifier', 'com.shunchuangvision.xhs-agent.runtime']
    for name in package_names:
        cmd += ['--collect-all', name]
    run([*cmd, HERE / 'runtime.py'])
    runtime = payload / 'launcher/deps' / args.version / 'xhs-runtime'
    runtime.parent.mkdir(parents=True)
    shutil.copy2(output / 'frozen/xhs-runtime', runtime); runtime.chmod(0o755)
    platform_name = core.platform_id()
    files = {p.relative_to(payload).as_posix(): p for p in payload.rglob('*') if p.is_file()
             and p.name != 'release-public.pem' and '__pycache__' not in p.parts}
    assert all(core.managed_path(n) for n in files)
    release = output / 'release'; release.mkdir()
    update = release / f'xhs-agent-{args.version}-{platform_name}-update.zip'
    with zipfile.ZipFile(update, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            path.chmod(0o755 if path == runtime else 0o644)
            archive.write(path, name)
    manifest = {'format': 1, 'app_id': core.APP_ID, 'platform': platform_name, 'version': args.version,
        'min_launcher_protocol': core.PROTOCOL, 'archive': update.name, 'size': update.stat().st_size,
        'sha256': core.sha256(update), 'files': {name: {'size': p.stat().st_size, 'sha256': core.sha256(p),
        'mode': 0o755 if p == runtime else 0o644} for name, p in sorted(files.items())}}
    raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    (release / f'update-manifest-{platform_name}.json').write_bytes(raw)
    core.atomic_json(payload / 'launcher-config.json', {'repository': core.REPOSITORY, 'port': 8001})
    core.atomic_json(payload / '.launcher/installed.json', {'version': args.version, 'files': sorted(files)})
    app = output / 'XHS-Agent.app'
    resources = app / 'Contents/Resources'; resources.mkdir(parents=True)
    shutil.copytree(payload, resources / 'payload')
    executable = app / 'Contents/MacOS/XHS-Agent'; executable.parent.mkdir()
    run(['clang', '-fobjc-arc', '-framework', 'Cocoa', '-mmacosx-version-min=14.0', HERE / 'bootstrap.m', '-o', executable])
    info = {'CFBundleName': 'XHS Agent', 'CFBundleDisplayName': 'XHS Agent',
        'CFBundleIdentifier': 'com.shunchuangvision.xhs-agent', 'CFBundleExecutable': 'XHS-Agent',
        'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': args.version, 'CFBundleVersion': args.version,
        'LSMinimumSystemVersion': '14.0', 'NSHighResolutionCapable': True,
        'NSHumanReadableCopyright': 'XHS Agent · 本地内容运营工作台'}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run(['codesign', '--force', '--deep', '--sign', '-', app])
    run(['codesign', '--verify', '--deep', '--strict', app])
    portable = release / f'xhs-agent-{args.version}-{platform_name}.zip'
    run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', app, portable])
    # DMG is a familiar drag-to-Applications installer; same .app as the zip.
    dmgroot = output / 'dmg-root'; dmgroot.mkdir()
    run(['ditto', app, dmgroot / app.name])
    (dmgroot / 'Applications').symlink_to('/Applications')
    shutil.copy2(HERE / 'README.md', dmgroot / '使用说明.txt')
    run(['hdiutil', 'create', '-volname', 'XHS Agent', '-srcfolder', dmgroot, '-ov', '-format', 'UDZO',
         release / f'xhs-agent-{args.version}-{platform_name}.dmg'])
    core.atomic_json(release / f'build-info-{platform_name}.json', {'version': args.version,
        'platform': platform_name, 'host': platform.mac_ver()[0], 'machine': platform.machine(),
        'python': platform.python_version(), 'minimum_macos': '14.0', 'apple_notarized': False,
        'source_version': args.base_version, 'source_sha256': core.sha256(output / 'source-windows.zip')})
    print('BUILD_OUTPUT=' + str(release), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--base-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args())
