"""Frozen Python dispatcher. Application code and user data live outside the .app."""
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import uuid

# PyInstaller analyzes this module; backend sources remain external and updateable.
if os.environ.get('_XHS_BUILD_ANALYSIS_ONLY') == '1':
    import freeze_imports


def data_root():
    override = os.environ.get('XHS_AGENT_DATA_HOME')
    return Path(override).expanduser().resolve() if override else Path.home() / 'Library/Application Support/XHS-Agent'


def bootstrap(payload, install_only=False, smoke=False):
    import fcntl
    root = data_root()
    root.parent.mkdir(parents=True, exist_ok=True)
    # First-install serialization, including concurrent Finder launches.
    with (root.parent / '.xhs-agent-bootstrap.lock').open('a+b') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if not root.exists():
            stage = root.with_name('.xhs-first-install-' + uuid.uuid4().hex)
            try:
                shutil.copytree(payload, stage)
                os.replace(stage, root)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        if not (root / 'version.json').is_file():
            raise RuntimeError('数据目录中缺少程序文件，请保留数据目录并联系维护者：' + str(root))
    sys.path.insert(0, str(root / 'launcher'))
    import core
    with core.installation_lock(root):
        core.recover_interrupted(root)
        info = core.current_version(root)
        if info.get('platform') != core.platform_id():
            raise RuntimeError('此数据目录的程序与当前芯片不一致，请先备份数据并安装对应芯片版本。')
        executable = core.python_path(root)
    if install_only:
        print(json.dumps({'root': str(root), 'version': info['version'], 'platform': info['platform']}))
        return
    # A new independent onefile process must not reuse the old process's temp directory.
    env = os.environ.copy()
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    os.chdir(root)
    args = [str(executable), '-s', str(root / 'launcher/ui.py')]
    if smoke:
        args.append('--smoke')
    os.execve(executable, args, env)


def main():
    if getattr(sys, 'frozen', False):
        import certifi
        # Every frozen child gets its own CA file, including after the launcher exits.
        os.environ['SSL_CERT_FILE'] = certifi.where()
        os.environ['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    args = sys.argv[1:]
    if args and args[0] == '--bootstrap':
        bootstrap(Path(args[1]).resolve(), '--install-only' in args, '--smoke' in args)
        return
    if args and args[0] == '-s':
        args.pop(0)
    # Preserve Python's -m worker invocation without requiring system Python.
    sys.path[:0] = [str(Path.cwd() / 'backend'), str(Path.cwd() / 'launcher')]
    if args and args[0] == '-m':
        sys.argv = args[1:]
        runpy.run_module(args[1], run_name='__main__', alter_sys=True)
    elif args:
        script = Path(args[0]).resolve()
        sys.path.insert(0, str(script.parent))
        sys.argv = [str(script), *args[1:]]
        runpy.run_path(str(script), run_name='__main__')
    else:
        raise RuntimeError('请从 XHS-Agent.app 启动。')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        root = data_root()
        root.parent.mkdir(parents=True, exist_ok=True)
        log = root.parent / 'XHS-Agent-startup.log'
        with log.open('a', encoding='utf-8') as stream:
            traceback.print_exc(file=stream)
        if '--bootstrap' in sys.argv:
            import tkinter as tk
            from tkinter import messagebox
            window = tk.Tk(); window.withdraw()
            messagebox.showerror('XHS Agent 启动未完成', '请查看启动日志：\n' + str(log))
            window.destroy()
        raise
