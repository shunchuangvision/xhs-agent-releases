"""Import an offline private model profile without altering the signed application bundle."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

FIELDS = ('LLM_PROVIDER', 'LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY', 'LLM_FALLBACK_MODEL')


class SetupError(Exception):
    pass


def valid_profile(values):
    try:
        base = values.get('LLM_BASE_URL') or ''
        parts = urlsplit(base)
        return bool(values.get('LLM_PROVIDER') == 'openai-compatible' and
            parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password and
            not parts.query and not parts.fragment and (values.get('LLM_MODEL') or '').strip() and
            (values.get('LLM_API_KEY') or '').strip())
    except ValueError:
        return False


def import_profile(root, profile_path, core):
    from dotenv import dotenv_values, set_key
    root = Path(root).resolve()
    path = core.contained(root, '.env')
    existing = dotenv_values(path, interpolate=False)
    # Keep any already configured provider, including local HTTP model servers.
    has_existing = (existing.get('LLM_PROVIDER') == 'openai-compatible' and
        all((existing.get(name) or '').strip() for name in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_API_KEY')))
    if has_existing:
        return 'kept-existing'
    values = dotenv_values(profile_path, interpolate=False)
    if set(values) - set(FIELDS) or not valid_profile(values):
        raise SetupError('私发配置文件不完整或格式无效，请联系提供者重新打包。')
    values = {name: values.get(name) or '' for name in FIELDS}
    if any(len(value) > 4096 or any(ord(char) < 32 for char in value) for value in values.values()):
        raise SetupError('私发配置内容格式无效，请联系提供者重新打包。')
    previous = path.read_bytes() if path.exists() else b''
    with core.installation_lock(root):
        was_running = core.stop_service(root)
        temporary = None
        try:
            fd, temporary_name = tempfile.mkstemp(prefix='.private-model-', suffix='.tmp', dir=root)
            temporary = Path(temporary_name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(previous)
            for name, value in values.items():
                set_key(temporary, name, value, quote_mode='always', encoding='utf-8')
            if (path.read_bytes() if path.exists() else b'') != previous:
                raise SetupError('配置已在其他窗口变化，本次未覆盖，请重新运行。')
            with temporary.open('r+b') as stream:
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if was_running:
                core.start_service(root)
    return 'imported'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    app = args.app.resolve()
    root = Path(os.environ['XHS_AGENT_DATA_HOME']).expanduser().resolve() if os.environ.get('XHS_AGENT_DATA_HOME') else Path.home() / 'Library/Application Support/XHS-Agent'
    # This invokes the original, signed native bootstrap and first-install lock.
    subprocess.run([str(app / 'Contents/MacOS/XHS-Agent'), '--install-only'],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    sys.path.insert(0, str(root / 'launcher'))
    import core
    result = import_profile(root, args.config, core)
    # No key, upstream URL, or process output is included in messages.
    print('已保留本机已有 API 配置。' if result == 'kept-existing' else 'API 配置已自动导入本机，后续更新会保留。')
    print(json.dumps({'status': result, 'version': core.current_version(root)['version']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # dotenv/provider errors can contain input; keep the error output generic.
        message = str(exc) if isinstance(exc, SetupError) else '自动配置未完成，请关闭生成任务或占用端口的其他实例后重试；已有配置和数据保留。'
        print(message, file=sys.stderr)
        raise SystemExit(1)
