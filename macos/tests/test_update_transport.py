"""Release discovery remains usable with REST API blocked; errors retain meaning."""
from email.message import Message
import io
import json
from pathlib import Path
import socket
import ssl
import sys
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import core
from test_updater import installation, package


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    core._UPDATE_CHECK_CACHE.clear()
    monkeypatch.setattr(core, 'platform_id', lambda: 'windows-x64')
    monkeypatch.setattr(core.urllib.request, 'getproxies', lambda: {'https': 'http://name:secret@127.0.0.1:7897'})
    monkeypatch.setattr(core.urllib.request, 'proxy_bypass', lambda host: False)


def discovery(installation, tmp_path, monkeypatch):
    root, key, public = installation
    archive, raw, sig, manifest = package(tmp_path, key, public)
    calls = []
    def fetch(url, *a):
        assert 'api.github.com' not in url, 'REST API must never be used'
        calls.append(url)
        if url.endswith('/latest/download/update-manifest.json'): return raw
        assert url.endswith('/download/v3.2.0/update-manifest.sig')
        return sig
    monkeypatch.setattr(core, 'fetch', fetch)
    return root, calls, raw, sig


def test_api_is_never_requested_and_archive_is_pinned(installation, tmp_path, monkeypatch):
    root, calls, raw, sig = discovery(installation, tmp_path, monkeypatch)
    update = core.check_update(root)
    assert len(calls) == 2
    assert update['url'] == f'https://github.com/{core.REPOSITORY}/releases/download/v3.2.0/xhs-agent-3.2.0-windows-x64-update.zip'
    assert '/tag/v3.2.0' in update['notes']


def test_repeat_checks_cache_only_verified_data_and_expire(installation, tmp_path, monkeypatch):
    root, calls, raw, sig = discovery(installation, tmp_path, monkeypatch)
    clock = [1000.]
    monkeypatch.setattr(core.time, 'monotonic', lambda: clock[0])
    core.check_update(root)
    core.check_update(root)
    assert len(calls) == 2
    clock[0] += 61
    core.check_update(root)
    assert len(calls) == 4
    core.atomic_json(root / 'version.json', {'version': '3.2.0'})
    assert core.check_update(root) is None and len(calls) == 4


def test_repository_change_does_not_reuse_other_repository_cache(installation, tmp_path, monkeypatch):
    root, calls, *_ = discovery(installation, tmp_path, monkeypatch)
    core.check_update(root)
    core.atomic_json(root / 'launcher-config.json', {'repository': 'another/repo'})
    core.check_update(root)
    assert len(calls) == 4 and '/another/repo/' in calls[-1]


def test_trust_change_does_not_reuse_verified_cache(installation, tmp_path, monkeypatch):
    root, calls, *_ = discovery(installation, tmp_path, monkeypatch)
    core.check_update(root)
    (root / 'launcher/release-public.pem').write_bytes(b'invalid replacement')
    with pytest.raises(core.LauncherError, match='签名'):
        core.check_update(root)
    assert len(calls) == 4


def test_bad_signature_stops_without_api_fallback_or_cache(installation, tmp_path, monkeypatch):
    root, key, public = installation
    _, raw, sig, _ = package(tmp_path, key, public)
    calls = []
    def fetch(url, *a):
        assert 'api.github.com' not in url
        calls.append(url)
        return raw + b' ' if url.endswith('.json') else sig
    monkeypatch.setattr(core, 'fetch', fetch)
    with pytest.raises(core.LauncherError, match='签名'): core.check_update(root)
    assert not core._UPDATE_CHECK_CACHE and len(calls) == 2


@pytest.mark.parametrize('raw', [b'[]', b'<html>proxy denied</html>', b'{"version":"../../bad"}', b'{"version":null}'])
def test_untrusted_version_cannot_choose_another_url(installation, monkeypatch, raw):
    root, *_ = installation
    calls = []
    monkeypatch.setattr(core, 'fetch', lambda url, *a: calls.append(url) or raw)
    with pytest.raises(core.LauncherError, match='清单格式'): core.check_update(root)
    assert len(calls) == 1 and not core._UPDATE_CHECK_CACHE


def http_failure(monkeypatch, status, headers=None, body=None):
    response_headers = Message()
    for name, value in (headers or {}).items(): response_headers[name] = value
    payload = json.dumps(body or {}).encode()
    class Opener:
        def open(self, request, **kwargs):
            raise HTTPError(request.full_url, status, 'synthetic', response_headers, io.BytesIO(payload))
    monkeypatch.setattr(core.urllib.request, 'build_opener', lambda *a: Opener())


def test_real_rate_limit_reports_reset_and_redacted_proxy(monkeypatch):
    monkeypatch.setattr(core.time, 'time', lambda: 1000.)
    http_failure(monkeypatch, 403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '2000'})
    with pytest.raises(core.LauncherError) as error: core.fetch('https://github.com/o/r', 8192)
    message = str(error.value)
    assert '请求限流' in message and '本机时间' in message and '127.0.0.1:7897' in message
    assert 'secret' not in message and 'name' not in message


def test_403_without_evidence_is_not_called_rate_limiting(monkeypatch):
    http_failure(monkeypatch, 403, body={'message': 'Forbidden'})
    with pytest.raises(core.LauncherError) as error: core.fetch('https://github.com/o/r', 8192)
    assert '拒绝访问' in str(error.value) and '未收到明确的限流信息' in str(error.value)


def test_secondary_rate_limit_and_retry_after(monkeypatch):
    http_failure(monkeypatch, 403, {'Retry-After': '120'}, {'message': 'You have exceeded a secondary rate limit'})
    with pytest.raises(core.LauncherError, match='120 秒'): core.fetch('https://github.com/o/r', 8192)


def test_expired_reset_does_not_promise_waiting_will_resolve(monkeypatch):
    monkeypatch.setattr(core.time, 'time', lambda: 3000.)
    http_failure(monkeypatch, 403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '2000'})
    with pytest.raises(core.LauncherError, match='恢复时间已过'): core.fetch('https://github.com/o/r', 8192)


@pytest.mark.parametrize('status,text', [(404, '签名更新文件'), (407, '代理要求认证'), (500, 'HTTP 500')])
def test_other_http_failures_have_distinct_messages(monkeypatch, status, text):
    http_failure(monkeypatch, status)
    with pytest.raises(core.LauncherError, match=text): core.fetch('https://github.com/o/r', 8192)


@pytest.mark.parametrize('reason,text', [(ConnectionRefusedError(61, 'refused'), '监听端口'),
    (socket.gaierror(-2, 'unknown'), '域名解析'), (TimeoutError(), '超时'),
    (ssl.SSLCertVerificationError('invalid'), '证书校验失败')])
def test_connection_failures_explain_route_and_cause(monkeypatch, reason, text):
    class Opener:
        def open(self, *a, **k): raise URLError(reason)
    monkeypatch.setattr(core.urllib.request, 'build_opener', lambda *a: Opener())
    with pytest.raises(core.LauncherError) as error: core.fetch('https://github.com/o/r', 8192)
    assert text in str(error.value) and '127.0.0.1:7897' in str(error.value)
    assert 'secret' not in str(error.value)


def test_proxy_bypass_describes_direct_connection(monkeypatch):
    monkeypatch.setattr(core.urllib.request, 'proxy_bypass', lambda host: True)
    assert core.network_route('https://github.com/o/r') == '直连 github.com'
