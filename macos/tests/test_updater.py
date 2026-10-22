import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from zipfile import ZipFile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'launcher'))
import core


@pytest.fixture(autouse=True)
def windows_compatibility(monkeypatch):
    # Keep the existing Windows signed-release format under regression coverage.
    monkeypatch.setattr(core, 'platform_id', lambda: 'windows-x64')


@pytest.fixture
def installation(tmp_path,monkeypatch):
    root=tmp_path/'应用';root.mkdir()
    core.atomic_json(root/'version.json',{'version':'3.1.0','dependencies':''})
    (root/'.env').write_text('LLM_API_KEY=local-test-only\n',encoding='utf-8')
    (root/'data/assets').mkdir(parents=True)
    (root/'data/assets/keep.png').write_bytes(b'keep asset')
    db=root/'data/xhs-agent.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE test(value TEXT)');conn.execute("INSERT INTO test VALUES ('keep data')")
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    (root/'launcher').mkdir()
    (root/'launcher/release-public.pem').write_bytes(public)
    (root/'backend/app').mkdir(parents=True)
    (root/'backend/app/main.py').write_text('OLD')
    (root/'frontend/dist').mkdir(parents=True)
    (root/'frontend/dist/index.html').write_text('OLD UI')
    core.atomic_json(root/'.launcher/installed.json',{'files':['version.json','backend/app/main.py','frontend/dist/index.html']})
    monkeypatch.setattr(core,'database_path',lambda _:db)
    return root,key,public


def package(tmp_path,key,public,files=None):
    files=files or {'version.json':json.dumps({'version':'3.2.0','dependencies':'3.2.0'}).encode(),
        'backend/app/main.py':b'NEW','frontend/dist/index.html':b'NEW UI','docs/new.md':b'guide'}
    archive=tmp_path/'xhs-agent-3.2.0-windows-x64-update.zip'
    with ZipFile(archive,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    manifest={'format':1,'app_id':'xhs-agent','platform':'windows-x64','version':'3.2.0','min_launcher_protocol':1,
        'archive':archive.name,'size':archive.stat().st_size,'sha256':core.sha256(archive),
        'files':{name:{'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()}}
    raw=json.dumps(manifest).encode()
    signature=json.dumps({'algorithm':'Ed25519','key_id':hashlib.sha256(public).hexdigest(),
        'signature':base64.b64encode(key.sign(raw)).decode()}).encode()
    return archive,raw,signature,manifest


def fake_lifecycle(monkeypatch,root,fail=False,running=True):
    state={'running':running,'version':'3.1.0','maintenance':False,'busy':False}
    def control(*args,**kwargs):
        if len(args)>1 and args[1]=='activate':state['maintenance']=False
        return state.copy() if state['running'] else None
    def stop(*args,**kwargs):
        result=state['running'];state['running']=False;return result
    def start(*args,**kwargs):
        state.update(running=True,version=core.current_version(root)['version'],maintenance=kwargs.get('maintenance',False))
        if fail and state['version']=='3.2.0':
            with sqlite3.connect(root/'data/xhs-agent.db') as db:db.execute("INSERT INTO test VALUES ('new migration')")
            raise core.LauncherError('simulated health failure')
        return state.copy()
    monkeypatch.setattr(core,'control',control);monkeypatch.setattr(core,'stop_service',stop);monkeypatch.setattr(core,'start_service',start)
    return state


def test_signature_and_valid_package(installation,tmp_path):
    root,key,public=installation
    archive,raw,sig,manifest=package(tmp_path,key,public)
    verified=core.verify_manifest(raw,sig,root/'launcher/release-public.pem')
    core.extract_verified(archive,verified,tmp_path/'extracted')
    assert (tmp_path/'extracted/backend/app/main.py').read_text()=='NEW'


def test_tampered_signature_and_download_rejected(installation,tmp_path):
    root,key,public=installation;archive,raw,sig,manifest=package(tmp_path,key,public)
    with pytest.raises(core.LauncherError,match='签名'):core.verify_manifest(raw+b' ',sig,root/'launcher/release-public.pem')
    archive.write_bytes(archive.read_bytes()+b'tampered')
    with pytest.raises(core.LauncherError,match='完整性'):core.extract_verified(archive,manifest,tmp_path/'bad')


@pytest.mark.parametrize('name',['../escape','.env','data/xhs-agent.db','launcher-config.json','launcher/release-public.pem',
    'launcher/runtime/python.exe','backend/app/C:stream','backend/app/CON.txt','backend/app/a.','/absolute','backend\\app\\evil.py'])
def test_protected_and_unsafe_paths_rejected(installation,tmp_path,name):
    root,key,public=installation
    files={'version.json':b'{}','backend/app/main.py':b'x','frontend/dist/index.html':b'x',name:b'evil'}
    _,raw,sig,_=package(tmp_path,key,public,files)
    with pytest.raises(core.LauncherError):core.verify_manifest(raw,sig,root/'launcher/release-public.pem')


def test_case_collision_is_rejected(installation,tmp_path):
    root,key,public=installation
    _,raw,sig,_=package(tmp_path,key,public,{'version.json':b'{}','backend/app/main.py':b'x','backend/app/MAIN.py':b'y','frontend/dist/index.html':b'x'})
    with pytest.raises(core.LauncherError):core.verify_manifest(raw,sig,root/'launcher/release-public.pem')


def test_update_preserves_configuration_data_assets_and_restarts(installation,tmp_path,monkeypatch):
    root,key,public=installation;archive,raw,sig,_=package(tmp_path,key,public)
    state=fake_lifecycle(monkeypatch,root)
    env=(root/'.env').read_bytes()
    with core.installation_lock(root):result=core.apply_package(root,archive,raw,sig)
    assert result=='3.2.0' and state['running'] and not state['maintenance']
    assert (root/'.env').read_bytes()==env and (root/'data/assets/keep.png').read_bytes()==b'keep asset'
    with sqlite3.connect(root/'data/xhs-agent.db') as db:assert db.execute('SELECT * FROM test').fetchall()==[('keep data',)]
    assert (root/'backend/app/main.py').read_text()=='NEW'
    assert core.read_json(root/'.launcher/transaction.json')['state']=='complete'
    with pytest.raises(core.LauncherError,match='降级'):core.apply_package(root,archive,raw,sig)


def test_health_failure_restores_code_and_database(installation,tmp_path,monkeypatch):
    root,key,public=installation;archive,raw,sig,_=package(tmp_path,key,public)
    state=fake_lifecycle(monkeypatch,root,fail=True)
    with pytest.raises(core.LauncherError,match='已恢复上一版本'):core.apply_package(root,archive,raw,sig)
    assert core.current_version(root)['version']=='3.1.0' and state['running']
    assert (root/'backend/app/main.py').read_text()=='OLD'
    assert not (root/'docs/new.md').exists()
    with sqlite3.connect(root/'data/xhs-agent.db') as db:assert db.execute('SELECT * FROM test').fetchall()==[('keep data',)]


def test_idle_install_leaves_service_stopped(installation,tmp_path,monkeypatch):
    root,key,public=installation;archive,raw,sig,_=package(tmp_path,key,public)
    state=fake_lifecycle(monkeypatch,root,running=False)
    core.apply_package(root,archive,raw,sig)
    assert not state['running']


def test_busy_service_rejects_before_changes(installation,tmp_path,monkeypatch):
    root,key,public=installation;archive,raw,sig,_=package(tmp_path,key,public)
    state=fake_lifecycle(monkeypatch,root);state['busy']=True
    with pytest.raises(core.LauncherError,match='任务'):core.apply_package(root,archive,raw,sig)
    assert (root/'backend/app/main.py').read_text()=='OLD' and state['running']
    assert not (root/'.launcher/transaction.json').exists()


def test_interrupted_update_recovers_before_next_action(installation,tmp_path,monkeypatch):
    root,key,public=installation;archive,raw,sig,_=package(tmp_path,key,public)
    fake_lifecycle(monkeypatch,root)
    def crash(*args,**kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(core,'start_service',crash)
    with pytest.raises(KeyboardInterrupt):core.apply_package(root,archive,raw,sig)
    assert core.read_json(root/'.launcher/transaction.json')['state']=='applying'
    assert (root/'backend/app/main.py').read_text()=='NEW'
    assert core.recover_interrupted(root)
    assert (root/'backend/app/main.py').read_text()=='OLD'


def test_lock_prevents_parallel_installation(installation):
    root,_,_=installation
    with core.installation_lock(root):
        with pytest.raises(core.LauncherError):
            with core.installation_lock(root):pass


@pytest.mark.parametrize('url',['http://github.com/a/b','https://evil.example/file.zip','https://github.com.evil.test/file','https://x:secret@github.com/a'])
def test_untrusted_download_hosts_rejected(url):
    with pytest.raises(core.LauncherError):core.https_url(url)


def test_github_latest_release_and_matching_assets(installation,tmp_path,monkeypatch):
    root,key,public=installation;archive,raw,sig,manifest=package(tmp_path,key,public)
    assets=[{'name':name,'browser_download_url':'https://github.com/o/r/releases/download/v3.2.0/'+name,
             'size':len(data),'digest':'sha256:'+hashlib.sha256(data).hexdigest()}
            for name,data in [('update-manifest.json',raw),('update-manifest.sig',sig),(archive.name,archive.read_bytes())]]
    release={'tag_name':'v3.2.0','assets':assets,'body':'release notes','draft':False,'prerelease':False}
    def fetch(url,*args):
        if url.endswith('/latest'):return json.dumps(release).encode()
        return raw if url.endswith('.json') else sig
    monkeypatch.setattr(core,'fetch',fetch)
    found=core.check_update(root)
    assert found['manifest']['version']=='3.2.0'
    core.atomic_json(root/'version.json',{'version':'3.2.0'})
    assert core.check_update(root) is None
    release['tag_name']='v3.9.0'
    with pytest.raises(core.LauncherError,match='不一致'):core.check_update(root)
