"""Service ownership, busy guards and non-responsive listeners during exit."""
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
import core


class Probe:
    def __init__(self, connected=False): self.connected = connected
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, timeout): pass
    def connect_ex(self, address): return 0 if self.connected else 10061


def test_already_stopped_can_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'control', lambda *a: None)
    monkeypatch.setattr(core.socket, 'socket', lambda: Probe())
    assert core.stop_service(tmp_path) is False


def test_unresponsive_listener_keeps_launcher_open(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'control', lambda *a: None)
    monkeypatch.setattr(core.socket, 'socket', lambda: Probe(True))
    with pytest.raises(core.LauncherError, match='暂不退出'):
        core.stop_service(tmp_path)


def test_shutdown_waits_for_exact_authenticated_pid(tmp_path, monkeypatch):
    calls, checks = [], []
    def control(root, action='status'):
        calls.append(action); return {'pid': 1234}
    monkeypatch.setattr(core, 'control', control)
    def alive(pid):
        checks.append(pid); return len(checks) == 1
    monkeypatch.setattr(core, 'process_alive', alive)
    monkeypatch.setattr(core.time, 'sleep', lambda *_: None)
    assert core.stop_service(tmp_path)
    assert calls == ['status', 'stop'] and checks == [1234, 1234]


@pytest.mark.parametrize('code,kind', [(401, core.ServiceConflict), (403, core.ServiceConflict),
                                      (404, core.ServiceConflict), (409, core.LauncherError),
                                      (500, core.LauncherError)])
def test_control_distinguishes_conflict_from_busy_or_failure(tmp_path, monkeypatch, code, kind):
    class Opener:
        def open(self, request, **kwargs):
            raise HTTPError(request.full_url, code, 'synthetic', {}, None)
    monkeypatch.setattr(core.urllib.request, 'build_opener', lambda *a: Opener())
    with pytest.raises(kind) as exc: core.control(tmp_path)
    if code in (409, 500): assert not isinstance(exc.value, core.ServiceConflict)


def test_wrong_root_never_sends_stop(tmp_path, monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a): return json.dumps({'root_id': 'different-installation', 'pid': 4567}).encode()
    requests = []
    class Opener:
        def open(self, req, **kwargs): requests.append(req.method); return Response()
    monkeypatch.setattr(core.urllib.request, 'build_opener', lambda *a: Opener())
    with pytest.raises(core.ServiceConflict): core.stop_service(tmp_path)
    assert requests == ['GET']
