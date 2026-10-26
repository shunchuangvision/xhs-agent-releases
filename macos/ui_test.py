"""Native UI acceptance with synthetic services; never calls a model provider."""
from pathlib import Path
import subprocess
import sys
import threading
import time


def exercise(ui, root, output):
    saved = {}
    def replace(obj, name, value):
        saved[obj, name] = getattr(obj, name)
        setattr(obj, name, value)
    opened, errors, relaunches = [], [], []
    replace(ui, 'ROOT', root)
    replace(ui.core, 'control', lambda *a, **k: None)
    replace(ui.core, 'recover_interrupted', lambda *a, **k: None)
    replace(ui.core, 'start_service', lambda *a, **k: {'pid': 123, 'version': '3.2.4'})
    replace(ui.core, 'stop_service', lambda *a, **k: None)
    replace(ui.webbrowser, 'open', lambda url: opened.append(url))
    replace(ui.messagebox, 'showerror', lambda *a, **k: errors.append(a))
    replace(ui.messagebox, 'showinfo', lambda *a, **k: None)
    replace(ui.messagebox, 'askyesno', lambda *a, **k: True)
    update = {'manifest': {'version': '3.2.5', 'size': 40 * 1048576}, 'notes': '用于界面验收的模拟更新。\n配置、文案和素材会保留。'}
    replace(ui.core, 'check_update', lambda *a, **k: update)
    replace(ui.core, 'install_update', lambda *a, **k: '3.2.5')
    window = None
    def wait(condition):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            window.update()
            if condition():
                window.update_idletasks()
                return
            time.sleep(.03)
        raise AssertionError('UI operation did not complete')
    def enabled(button):
        return str(button.cget('state')) != 'disabled'
    def layout():
        window.update_idletasks()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        controls = window.buttons + [w for w in descendants(window) if isinstance(w, ui.tk.Label)]
        for button in controls:
            assert button.winfo_ismapped(), button.cget('text')
            assert button.winfo_height() >= button.winfo_reqheight(), ('height', button.cget('text'))
            assert button.winfo_width() >= button.winfo_reqwidth(), ('width', button.cget('text'))
            assert button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    def capture(name):
        window.lift(); window.update(); time.sleep(.25)
        path = output / name
        x, y, w, h = window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height()
        if sys.platform == 'darwin':
            subprocess.run(['/usr/sbin/screencapture', '-x', '-R', f'{x},{y},{w},{h}', str(path)], check=True, timeout=10)
        else:
            from PIL import ImageGrab
            ImageGrab.grab(bbox=(x, y, x+w, y+h)).save(path)
    try:
        window = ui.Launcher()
        window.relaunch = lambda: relaunches.append(True)
        wait(lambda: window.ready and not window.working)
        assert enabled(window.launch_button) and not enabled(window.stop_button)
        assert not enabled(window.install_button) and not enabled(window.notes_button)
        layout(); capture('launcher-ready.png')
        window.geometry('900x700'); window.update(); layout()
        window.launch_button.invoke(); wait(lambda: not window.working)
        assert window.running and enabled(window.stop_button) and len(opened) == 1
        assert '打开工作台' in window.launch_button.cget('text')
        window.stop_button.invoke(); wait(lambda: not window.working)
        assert not window.running and not enabled(window.stop_button)
        window.check_button.invoke(); wait(lambda: not window.working)
        assert enabled(window.install_button) and enabled(window.notes_button)
        window.notes_button.invoke(); window.update()
        dialogs = [w for w in window.winfo_children() if isinstance(w, ui.tk.Toplevel)]
        assert len(dialogs) == 1
        dialogs[0].destroy()
        window.geometry('920x700'); window.update(); layout(); capture('launcher-update.png')
        window.install_button.invoke(); wait(lambda: not window.working)
        assert relaunches and not enabled(window.install_button)
        # Failure after finding a version must not keep an old install action available.
        def fail(*a, **k):
            raise ui.core.LauncherError('模拟网络断开，请重试')
        ui.core.check_update = fail
        window.check_button.invoke(); wait(lambda: not window.working)
        assert errors and not enabled(window.install_button) and not enabled(window.notes_button)
        assert '无法' in window.release_title.get()
        # Controls remain locked while an operation is in progress, then recover on retry.
        gate = threading.Event()
        ui.core.check_update = lambda *a, **k: (gate.wait(5), None)[1]
        window.check_button.invoke(); window.update()
        layout()
        assert window.working and all(not enabled(b) for b in window.buttons)
        window.close(); assert not window._closed
        gate.set(); wait(lambda: not window.working)
        assert '最新' in window.release_title.get() and enabled(window.check_button)
        assert not enabled(window.install_button)
        # Closing cancels callbacks; no callbacks from destroyed windows affect the next launch.
        window.close(); assert window._closed and not window._timers
        return {'ui_state_transitions': True, 'ui_error_retry': True, 'ui_busy_guard': True,
                'ui_layout_at_minimum_size': True, 'ui_screenshots': True, 'external_model_calls': 0}
    finally:
        if window is not None and not window._closed:
            window.destroy()
        for (obj, name), value in saved.items():
            setattr(obj, name, value)


if __name__ == '__main__':
    import json
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'launcher'))
    import ui
    output = Path(sys.argv[1]).resolve(); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='xhs-ui-', dir=output) as folder:
        root = Path(folder)
        (root / 'version.json').write_text(json.dumps({'version': '3.2.4'}))
        result = exercise(ui, root, output)
    (output / 'ui-test-result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
