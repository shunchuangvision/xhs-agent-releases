"""Verify production GitHub assets from the distributed native runtime, on synthetic data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request


def main():
    root = Path(os.environ['XHS_AGENT_DATA_HOME']).resolve()
    if not getattr(sys, 'frozen', False):
        executable = Path('download/XHS-Agent.app/Contents/MacOS/XHS-Agent').resolve()
        subprocess.run([executable, '--install-only'], check=True, timeout=90)
        info = json.loads((root / 'version.json').read_text())
        runtime = root / 'launcher/deps' / info['dependencies'] / 'xhs-runtime'
        subprocess.run([runtime, '-s', Path(__file__).resolve()], cwd=root, check=True, timeout=240)
        return
    sys.path.insert(0, str(root / 'launcher'))
    import core
    core.atomic_json(root / 'launcher-config.json', {'repository': core.REPOSITORY, 'port': 18019})
    (root / '.env').write_text('LLM_PROVIDER=demo\nV3_EMBEDDED_WORKER=1\n')
    before_env = (root / '.env').read_bytes()
    initial = core.current_version(root)
    old = dict(initial, version='3.1.9')
    core.atomic_json(root / 'version.json', old)
    try:
        core.start_service(root, progress=print)
        url = 'http://127.0.0.1:18019/api/projects'
        request = urllib.request.Request(url, data=json.dumps({'product_name': '正式更新保留验证', 'category': '测试',
            'audience': '测试', 'facts': {}}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=20) as response:
            project = json.load(response)
        update = core.check_update(root, print)
        assert update and update['manifest']['platform'] == core.platform_id()
        assert update['manifest']['version'] == initial['version']
        core.install_update(root, update, print)
        assert (root / '.env').read_bytes() == before_env
        with urllib.request.urlopen(url + '/' + project['id'] + '/workspace', timeout=20) as response:
            assert json.load(response)['project']['id'] == project['id']
        assert core.check_update(root) is None
        result = {'status': 'passed', 'version': initial['version'], 'platform': core.platform_id(),
            'public_github_download': True, 'production_signature': True, 'config_preserved': True,
            'project_preserved': True, 'latest_check_after_update': True, 'external_model_calls': 0}
        (root.parent / ('published-test-' + core.platform_id() + '.json')).write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        core.stop_service(root)


if __name__ == '__main__':
    main()
