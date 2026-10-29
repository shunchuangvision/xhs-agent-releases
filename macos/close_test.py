"""Real packaged service shutdown through native window close and macOS Quit."""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time


def processes():
    if os.name == 'nt':
        raw = subprocess.check_output(['powershell.exe', '-NoProfile', '-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress'],
            text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        return [(x['ProcessId'], x['ParentProcessId'], '') for x in json.loads(raw)]
    raw = subprocess.check_output(['/bin/ps', '-axo', 'pid=,ppid=,stat='], text=True, timeout=10)
    return [(int(p), int(parent), state) for line in raw.splitlines() if line.strip()
            for p, parent, state in [line.split(None, 2)]]


def children_of(pid):
    rows = processes(); owned = {pid}
    while True:
        more = {child for child, parent, state in rows if parent in owned and child not in owned}
        if not more: return owned - {pid}
        owned.update(more)


def exercise(ui, root):
    core = ui.core
    previous = ui.ROOT, ui.messagebox.showerror, ui.messagebox.showinfo
    ui.ROOT = root
    errors = []
    ui.messagebox.showerror = lambda *a, **k: errors.append(a)
    ui.messagebox.showinfo = lambda *a, **k: None
    window = None
    def pump(condition, timeout=50):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            window.update(); time.sleep(.03)
        assert condition(), 'Launcher close did not complete'
    def busy(enabled):
        with sqlite3.connect(core.database_path(root)) as db:
            if enabled:
                db.execute('INSERT INTO records (id,kind,project_id,data,created_at) VALUES (?,?,?,?,?)',
                    ('close-acceptance-task', 'task', 'synthetic', json.dumps({'status': 'running'}), '2026-10-29T00:00:00'))
            else:
                db.execute("DELETE FROM records WHERE id='close-acceptance-task'")
    try:
        modes = ('window', 'mac_quit') if sys.platform == 'darwin' else ('window',)
        for mode in modes:
            state = core.start_service(root)
            pid = state['pid']; deadline = time.monotonic() + 8
            owned = children_of(pid)
            while not owned and time.monotonic() < deadline:
                time.sleep(.2); owned = children_of(pid)
            assert owned, 'Embedded worker was not started'
            window = ui.Launcher(); window.withdraw()
            pump(lambda: window.ready and not window.working)
            assert window.running
            if mode == 'window':
                busy(True); window.close(); pump(lambda: not window.working)
                assert errors and not window._closed and core.process_alive(pid)
                busy(False)
            if mode == 'mac_quit':
                window.tk.call('tk::mac::Quit')
            else:
                window.close()
            pump(lambda: window._closed)
            assert not core.process_alive(pid)
            deadline = time.monotonic() + 8
            while True:
                active = {p for p, parent, state in processes() if not state.startswith('Z')}
                if not owned.intersection(active): break
                assert time.monotonic() < deadline, 'Worker remained active after exit'
                time.sleep(.2)
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(('127.0.0.1', core.config(root)['port'])) != 0
            assert not window._timers
        return {'launcher_close_stops_real_service': True, 'launcher_close_stops_real_worker': True,
                'launcher_close_releases_port': True, 'launcher_close_busy_guard': True,
                'mac_quit_stops_service': sys.platform == 'darwin'}
    finally:
        busy(False)
        if window and not window._closed: window.destroy()
        core.stop_service(root)
        ui.ROOT, ui.messagebox.showerror, ui.messagebox.showinfo = previous
