"""Publish only tested Mac build outputs; production signing is still offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent / 'launcher'))
import core


def publish(folder, version):
    core.version_tuple(version)
    assets = []
    for target in ('macos-arm64', 'macos-x64'):
        paths = list(folder.rglob(f'update-manifest-{target}.json'))
        assert len(paths) == 1
        path = paths[0]; manifest = json.loads(path.read_bytes())
        assert manifest['version'] == version and manifest['platform'] == target
        assert manifest['archive'] == f'xhs-agent-{version}-{target}-update.zip'
        evidence = json.loads((path.parent / f'test-result-{target}.json').read_bytes())
        assert evidence['status'] == 'passed' and evidence['finder_bootstrap_and_reopen']
        archive = path.parent / manifest['archive']
        assert core.sha256(archive) == manifest['sha256'] and archive.stat().st_size == manifest['size']
        with zipfile.ZipFile(archive) as bundle:
            assert set(bundle.namelist()) == manifest['files'].keys()
            for name, info in manifest['files'].items():
                assert core.managed_path(name)
                core.contained(folder, name)
                data = bundle.read(name)
                assert len(data) == info['size'] and hashlib.sha256(data).hexdigest() == info['sha256']
        portable = path.parent / f'xhs-agent-{version}-{target}.zip'
        with zipfile.ZipFile(portable) as bundle:
            names = bundle.namelist()
            assert all(not n.endswith(('/.env', '/control.json', '.db', 'private.pem')) for n in names)
            public = bundle.read('XHS-Agent.app/Contents/Resources/payload/launcher/release-public.pem')
            assert public == (Path(__file__).resolve().parent / 'launcher/release-public.pem').read_bytes()
        platform_assets = [path, archive, portable, path.parent / f'xhs-agent-{version}-{target}.dmg']
        assert all(p.is_file() for p in platform_assets)
        checksum = path.parent / f'SHA256SUMS-{target}.txt'
        checksum.write_text(''.join(core.sha256(p) + '  ' + p.name + '\n' for p in platform_assets))
        assets.extend([*platform_assets, checksum])
    # Existing Windows assets are never included in this upload list.
    subprocess.run(['gh', 'release', 'upload', 'v' + version, '--repo', core.REPOSITORY,
                    *map(str, assets)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('version')
    args = parser.parse_args()
    publish(args.folder, args.version)
