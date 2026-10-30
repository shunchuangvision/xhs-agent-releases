import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from zipfile import ZipFile
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import core


def signed(tmp_path, target='macos-arm64', mode=0o755):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    trust = tmp_path / 'public.pem'; trust.write_bytes(public)
    files = {'version.json': b'{"version":"3.2.0","dependencies":"3.2.0"}',
             'backend/app/main.py': b'new', 'frontend/dist/index.html': b'new',
             'launcher/deps/3.2.0/xhs-runtime': b'native executable'}
    archive = tmp_path / f'xhs-agent-3.2.0-{target}-update.zip'
    with ZipFile(archive, 'w') as bundle:
        for name, value in files.items():
            bundle.writestr(name, value)
    manifest = {'format': 1, 'app_id': core.APP_ID, 'platform': target, 'version': '3.2.0', 'min_launcher_protocol': 1,
                'archive': archive.name, 'size': archive.stat().st_size, 'sha256': core.sha256(archive),
                'files': {n: {'size': len(v), 'sha256': hashlib.sha256(v).hexdigest(),
                             'mode': mode if n.endswith('/xhs-runtime') else 0o644} for n, v in files.items()}}
    raw = json.dumps(manifest).encode()
    sig = json.dumps({'algorithm': 'Ed25519', 'key_id': hashlib.sha256(public).hexdigest(),
                      'signature': base64.b64encode(key.sign(raw)).decode()}).encode()
    return raw, sig, trust, archive


def test_mac_runtime_extraction_preserves_executable_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'platform_id', lambda: 'macos-arm64')
    raw, sig, trust, archive = signed(tmp_path)
    manifest = core.verify_manifest(raw, sig, trust)
    core.extract_verified(archive, manifest, tmp_path / 'target')
    runtime = tmp_path / 'target/launcher/deps/3.2.0/xhs-runtime'
    assert runtime.read_bytes() == b'native executable'
    if os.name != 'nt':
        assert runtime.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize('target', ['macos-x64', 'windows-x64'])
def test_different_architecture_is_rejected(tmp_path, monkeypatch, target):
    monkeypatch.setattr(core, 'platform_id', lambda: 'macos-arm64')
    raw, sig, trust, _ = signed(tmp_path, target)
    with pytest.raises(core.LauncherError):
        core.verify_manifest(raw, sig, trust)


@pytest.mark.parametrize('mode', [0o644, 0o777, 0o4755])
def test_invalid_runtime_permissions_rejected(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(core, 'platform_id', lambda: 'macos-arm64')
    raw, sig, trust, _ = signed(tmp_path, mode=mode)
    with pytest.raises(core.LauncherError):
        core.verify_manifest(raw, sig, trust)


def test_mixed_platform_release_selects_own_signed_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'platform_id', lambda: 'macos-arm64')
    raw, sig, trust, archive = signed(tmp_path)
    root = tmp_path / 'app'; (root / 'launcher').mkdir(parents=True)
    (root / 'launcher/release-public.pem').write_bytes(trust.read_bytes())
    core.atomic_json(root / 'version.json', {'version': '3.1.9', 'dependencies': '3.1.9'})
    prefix = f'https://github.com/{core.REPOSITORY}/releases/'
    responses = {prefix+'latest/download/update-manifest-macos-arm64.json': raw,
                 prefix+'download/v3.2.0/update-manifest-macos-arm64.sig': sig}
    monkeypatch.setattr(core, 'fetch', lambda url, *args: responses[url])
    update = core.check_update(root)
    assert update['manifest']['platform'] == 'macos-arm64'
    assert update['url'] == prefix+'download/v3.2.0/'+archive.name
