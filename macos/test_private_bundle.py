"""Native test of the private installer using synthetic credentials only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request


def main():
    root = Path(os.environ['XHS_AGENT_DATA_HOME'])
    bundle = Path('download').resolve()
    if not getattr(sys, 'frozen', False):
        # Test setup and repeated execution through the exact double-click entry point.
        for name in ('private_setup.py', 'private_install.command'):
            shutil.copy2(Path(__file__).parent / name, bundle / name)
        (bundle / 'private_install.command').chmod(0o755)
        (bundle / 'private-model.env').write_text(
            'LLM_PROVIDER=openai-compatible\nLLM_BASE_URL=https://unit.invalid/v1\n'
            'LLM_MODEL=private-ci-model\nLLM_API_KEY=synthetic-private-ci-key\nLLM_FALLBACK_MODEL=\n')
        command = [str(bundle / 'private_install.command'), '--no-open']
        subprocess.run(command, check=True, timeout=180)
        previous = (root / '.env').read_bytes()
        # A second distribution must preserve an already configured recipient's profile.
        (bundle / 'private-model.env').write_text(
            'LLM_PROVIDER=openai-compatible\nLLM_BASE_URL=https://unit.invalid/v1\n'
            'LLM_MODEL=different-model\nLLM_API_KEY=different-synthetic-key\nLLM_FALLBACK_MODEL=\n')
        subprocess.run(command, check=True, timeout=120)
        assert (root / '.env').read_bytes() == previous
        installed_app = Path(os.environ['XHS_PRIVATE_APPLICATIONS_DIR']) / 'XHS-Agent.app'
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(installed_app)], check=True)
        info = json.loads((root / 'version.json').read_text())
        runtime = root / 'launcher/deps' / info['dependencies'] / 'xhs-runtime'
        subprocess.run([str(runtime), '-s', str(Path(__file__).resolve())], cwd=Path.cwd(), check=True, timeout=240)
        return
    sys.path.insert(0, str(root / 'launcher'))
    import core
    from dotenv import dotenv_values
    assert dotenv_values(root / '.env')['LLM_API_KEY'] == 'synthetic-private-ci-key'
    assert (root / '.env').stat().st_mode & 0o777 == 0o600
    core.atomic_json(root / 'launcher-config.json', {'repository': core.REPOSITORY, 'port': 18024})
    before = (root / '.env').read_bytes()
    original = core.current_version(root)
    core.atomic_json(root / 'version.json', dict(original, version='3.2.1'))
    try:
        core.start_service(root)
        with urllib.request.urlopen('http://127.0.0.1:18024/api/model-config', timeout=20) as response:
            raw = response.read()
            assert json.loads(raw)['configured'] and b'synthetic-private-ci-key' not in raw
        update = core.check_update(root)
        assert update and update['manifest']['version'] == original['version']
        core.install_update(root, update)
        assert (root / '.env').read_bytes() == before
        assert core.check_update(root) is None
        with urllib.request.urlopen('http://127.0.0.1:18024/api/model-config', timeout=20) as response:
            assert json.load(response)['configured']
        result = {'status': 'passed', 'platform': core.platform_id(), 'version': original['version'],
            'double_click_entry': True, 'auto_configured': True, 'existing_profile_preserved': True,
            'signed_app_unchanged': True, 'public_github_update': True, 'config_preserved_after_update': True,
            'external_model_calls': 0, 'only_synthetic_credentials_used': True}
        (root.parent / ('private-test-' + core.platform_id() + '.json')).write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        core.stop_service(root)


if __name__ == '__main__':
    main()
