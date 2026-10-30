"""Exercise the actual frozen runtime on each native GitHub macOS runner."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from zipfile import ZipFile


def outer(build):
    from safari_test import exercise as check_safari
    safari_result = check_safari(build)
    # Run the provider stub in the host Python, independent of the frozen application.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.headers.get('Authorization') != 'Bearer mac-ci-synthetic-key':
                self.send_response(401); self.end_headers(); return
            from media_test import provider_reply
            body = json.dumps(provider_reply(self.path, raw)).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    provider = ThreadingHTTPServer(('127.0.0.1', 18022), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    env = os.environ.copy()
    root = build / 'qa/用户资料 XHS Agent'
    env['XHS_AGENT_DATA_HOME'] = str(root)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    # Launch the actual .app native entry point and its first-install path.
    bootstrap = build / 'XHS-Agent.app/Contents/MacOS/XHS-Agent'
    subprocess.run([bootstrap, '--smoke'], env=env, check=True, timeout=90)
    # A complete Mac installation includes the same browser capture extension as Windows.
    with ZipFile(build / 'source-windows.zip') as archive:
        extension_files = [name for name in archive.namelist() if name.startswith('extension/')]
        assert extension_files
        for name in extension_files:
            assert (root / name).read_bytes() == archive.read(name)
    info = json.loads((root / 'version.json').read_text())
    original_runtime = root / 'launcher/deps' / info['dependencies'] / 'xhs-runtime'
    old_runtime = root / 'launcher/deps/3.1.9/xhs-runtime'
    old_runtime.parent.mkdir(parents=True)
    shutil.copy2(original_runtime, old_runtime)
    info.update(version='3.1.9', dependencies='3.1.9')
    (root / 'version.json').write_text(json.dumps(info))
    original_runtime.unlink()
    subprocess.run([old_runtime, '-s', Path(__file__).resolve(), build, '--inside', root],
                   env=env, cwd=root, check=True, timeout=200)
    # Opening the originally downloaded .app must retain the upgraded installation.
    before = (root / '.env').read_bytes()
    subprocess.run([bootstrap, '--smoke'], env=env, check=True, timeout=90)
    assert (root / '.env').read_bytes() == before
    result = build / 'release' / ('test-result-' + json.loads((root / 'version.json').read_text())['platform'] + '.json')
    value = json.loads(result.read_text()); value['finder_bootstrap_and_reopen'] = True
    value['browser_extension_included'] = True
    value.update(safari_result)
    result.write_text(json.dumps(value, indent=2))
    provider.shutdown()


def inside(build, root):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    sys.path.insert(0, str(root / 'launcher'))
    import core
    target_platform = core.platform_id()
    release = build / 'release'
    raw = (release / f'update-manifest-{target_platform}.json').read_bytes()
    manifest = json.loads(raw)
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    # Tests use a throwaway trust anchor in the isolated QA directory only.
    (root / 'launcher/release-public.pem').write_bytes(public)
    sig = json.dumps({'algorithm': 'Ed25519', 'key_id': hashlib.sha256(public).hexdigest(),
                     'signature': base64.b64encode(key.sign(raw)).decode()}).encode()
    core.atomic_json(root / 'launcher-config.json', {'repository': core.REPOSITORY, 'port': 18018})
    (root / '.env').write_text('LLM_PROVIDER=demo\nAPP_TOKEN=mac-ci-only\nV3_EMBEDDED_WORKER=1\n')
    before_env = (root / '.env').read_bytes()
    base = 'http://127.0.0.1:18018'

    def api(path, method='GET', body=None):
        request = urllib.request.Request(base + '/api' + path, method=method,
            headers={'Authorization': 'Bearer mac-ci-only', 'Content-Type': 'application/json'},
            data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def records():
        with sqlite3.connect(root / 'data/xhs-agent.db') as db:
            return dict(db.execute('SELECT id,data FROM records'))

    try:
        state = core.start_service(root, progress=print)
        assert state['version'] == '3.1.9'
        assert api('/health')['status'] == 'ok'
        assert urllib.request.urlopen(base, timeout=10).status == 200
        try:
            urllib.request.urlopen(base + '/_launcher/status')
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        else:
            raise AssertionError('Unauthenticated control accepted')
        project = api('/projects', 'POST', {'product_name': 'Mac 验收保温杯', 'category': '生活好物',
                                          'audience': '通勤人群', 'facts': {'容量': '350ml'}})
        api('/projects/' + project['id'] + '/generate', 'POST', {'count': 1})
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            workspace = api('/projects/' + project['id'] + '/workspace')
            if workspace['copies'] and workspace['tasks'][0]['status'] == 'succeeded':
                break
            time.sleep(.4)
        assert len(workspace['copies']) == 1
        before = records()
        with sqlite3.connect(root / 'data/xhs-agent.db') as db:
            db.execute('INSERT INTO records(id,kind,project_id,created_at,data) VALUES (?,?,?,?,?)',
                       ('qa-busy', 'task', project['id'], '2026-10-22', '{"status":"running"}'))
        try:
            core.stop_service(root)
        except core.LauncherError as exc:
            assert '任务' in str(exc)
        else:
            raise AssertionError('Busy service was stopped')
        with sqlite3.connect(root / 'data/xhs-agent.db') as db:
            db.execute('DELETE FROM records WHERE id=?', ('qa-busy',))
        config = api('/model-config')
        assert not config['configured']
        saved = api('/model-config', 'PUT', {'base_url': 'http://127.0.0.1:18022/v1',
            'model': 'mac-ci-model', 'api_key': 'mac-ci-synthetic-key', 'revision': config['revision']})
        assert saved['configured'] and 'mac-ci-synthetic-key' not in json.dumps(saved)
        assert api('/health')['model'] == 'mac-ci-model'
        from reference_test import configure, exercise as exercise_media
        configure(api)
        session_marker = root / 'data/browser-session/xiaohongshu/synthetic-preservation.txt'
        session_marker.parent.mkdir(parents=True, exist_ok=True)
        session_marker.write_text('synthetic browser profile marker')
        before_env = (root / '.env').read_bytes()
        assert b'APP_TOKEN=mac-ci-only' in before_env
        sys.path.insert(0, str(Path(__file__).resolve().parent / 'manual-update'))
        from manual_upgrade import upgrade as manual_upgrade
        def fetch_manual(url, target, limit):
            assert 'api.github.com' not in url
            if target.suffix == '.sig':
                target.write_bytes(sig)
            else:
                shutil.copy2(release / target.name, target)
        subprocess.run(['/bin/bash', '-n', str(Path(__file__).resolve().parent / 'manual-update/XHS-Agent-Mac-Update.command')], check=True)
        assert manual_upgrade(root, core, fetch_manual) == 'updated' 
        assert not core.process_alive(state['pid'])
        assert core.current_version(root)['version'] == manifest['version']
        assert core.control(root)['maintenance'] is False
        assert (root / '.env').read_bytes() == before_env
        assert session_marker.read_text() == 'synthetic browser profile marker'
        assert api('/model-config')['configured']
        assert api('/health')['model'] == 'mac-ci-model'
        assert core.config(root)['port'] == 18018
        assert all(records()[k] == v for k, v in before.items())
        assert core.python_path(root).parent.name == manifest['version']
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            console = api('/v3/projects/' + project['id'] + '/console')
            if console['worker']['online']:
                break
            time.sleep(.5)
        assert console['worker']['online']
        media_result = exercise_media(api, base, 'mac-ci-only', project['id'])
        core.stop_service(root, print)
        import ui
        ui.ROOT = root
        window = ui.Launcher(); window.withdraw()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            window.update(); time.sleep(.05)
        assert not window.working and window.state_text.get() == '服务未启动'
        window.destroy()
        from ui_test import exercise
        ui_result = exercise(ui, root, release)
        from close_test import exercise as check_close
        close_result = check_close(ui, root)
        # Restore the production public key for subsequent first-launch smoke test.
        shutil.copy2(build / 'payload/launcher/release-public.pem', root / 'launcher/release-public.pem')
        real_fetch = core.fetch
        release_requests = []
        def no_api(url, *a, **k):
            assert 'api.github.com' not in url, 'REST API must not be required'
            release_requests.append(url)
            return real_fetch(url, *a, **k)
        core.fetch = no_api
        core._UPDATE_CHECK_CACHE.clear()
        try:
            core.check_update(root)
            assert len(release_requests) == 2
        finally:
            core.fetch = real_fetch
            core._UPDATE_CHECK_CACHE.clear()
        result = {'status': 'passed', 'platform': target_platform, 'generation': True, 'native_tk_ui': True,
                  'signed_upgrade': True, 'separate_runtime_version': True, 'data_preserved': True,
                  'config_preserved': True, 'private_control': True, 'busy_stop_protection': True,
                  'worker_after_upgrade': True, 'web_model_setup': True, 'saved_model_after_upgrade': True,
                  'external_model_calls': 0, 'manual_signed_upgrade': True, 'update_without_rest_api': True}
        result.update(ui_result)
        result.update(media_result)
        result.update(close_result)
        (release / f'test-result-{target_platform}.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        try:
            core.stop_service(root)
        except Exception:
            pass


if __name__ == '__main__':
    build = Path(sys.argv[1]).resolve()
    if '--inside' in sys.argv:
        inside(build, Path(sys.argv[-1]).resolve())
    else:
        outer(build)
