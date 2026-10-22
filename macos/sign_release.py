"""Offline publisher step: verify downloaded artifacts, then sign per-platform manifests."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import zipfile
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parent / 'launcher'))
import core


def sign(folder, key_path):
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert public == (Path(__file__).resolve().parent / 'launcher/release-public.pem').read_bytes()
    manifests = sorted(folder.rglob('update-manifest-macos-*.json'))
    assert len(manifests) == 2, 'Both native architectures must be present'
    for path in manifests:
        raw = path.read_bytes(); manifest = json.loads(raw); platform_name = manifest['platform']
        assert platform_name in ('macos-arm64', 'macos-x64')
        evidence = json.loads((path.parent / f'test-result-{platform_name}.json').read_text())
        assert evidence['status'] == 'passed' and evidence['finder_bootstrap_and_reopen']
        signature = json.dumps({'algorithm': 'Ed25519', 'key_id': hashlib.sha256(public).hexdigest(),
            'signature': base64.b64encode(key.sign(raw)).decode()}, indent=2).encode()
        previous = core.platform_id
        core.platform_id = lambda: platform_name
        try:
            verified = core.verify_manifest(raw, signature, Path(__file__).resolve().parent / 'launcher/release-public.pem')
        finally:
            core.platform_id = previous
        archive = path.parent / verified['archive']
        assert core.sha256(archive) == verified['sha256'] and archive.stat().st_size == verified['size']
        with zipfile.ZipFile(archive) as bundle:
            assert set(bundle.namelist()) == verified['files'].keys()
            for name, info in verified['files'].items():
                data = bundle.read(name)
                assert len(data) == info['size'] and hashlib.sha256(data).hexdigest() == info['sha256']
                assert not name.startswith(('data/', '.launcher/')) and name != '.env'
        path.with_suffix('.sig').write_bytes(signature)
        assets = [p for p in path.parent.iterdir() if p.is_file() and p.suffix in ('.dmg', '.zip', '.json', '.sig')]
        (path.parent / f'SHA256SUMS-{platform_name}.txt').write_text(
            ''.join(core.sha256(p) + '  ' + p.name + '\n' for p in sorted(assets)), encoding='utf-8')
        print(platform_name + ': verified and signed ' + verified['version'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--signing-key', type=Path, required=True)
    args = parser.parse_args()
    sign(args.folder, args.signing_key)
