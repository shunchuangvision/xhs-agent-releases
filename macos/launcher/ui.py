"""Small native launcher; all network/install work stays off the UI thread."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import traceback
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'launcher'))
import core
FONT = 'PingFang SC' if sys.platform == 'darwin' else 'Microsoft YaHei UI'


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('XHS Agent · 启动器')
        self.geometry('780x620')
        self.minsize(700, 570)
        self.configure(bg='#f4f7f5')
        self.events = queue.Queue()
        self.working = False
        self.update_info = None
        self.buttons = []
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TButton', font=(FONT, 10), padding=(15, 10))
        style.configure('Primary.TButton', background='#245c4d', foreground='white')
        style.map('Primary.TButton', background=[('active', '#34715e'), ('disabled', '#90a99e')])
        style.configure('TLabel', background='#f4f7f5', font=(FONT, 10))
        style.configure('TFrame', background='#f4f7f5')
        outer = ttk.Frame(self, padding=28)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='XHS Agent', font=(FONT, 26, 'bold'), foreground='#245c4d').pack(anchor='w')
        ttk.Label(outer, text='启动你的内容工作台，持续获取新功能', foreground='#748579').pack(anchor='w', pady=(4, 20))
        self.version = tk.StringVar(value='当前版本  ' + core.current_version(ROOT)['version'])
        self.state_text = tk.StringVar(value='正在检查本地服务…')
        row = ttk.Frame(outer); row.pack(fill='x')
        ttk.Label(row, textvariable=self.version, font=(FONT, 11, 'bold')).pack(side='left')
        ttk.Label(row, textvariable=self.state_text, foreground='#607366').pack(side='right')
        actions = ttk.Frame(outer); actions.pack(fill='x', pady=(18, 14))
        self.button(actions, '启动并打开系统', self.launch, 'Primary.TButton')
        self.button(actions, '打开页面', self.open_page)
        self.button(actions, '停止服务', self.stop)
        ttk.Separator(outer).pack(fill='x', pady=8)
        update_row = ttk.Frame(outer); update_row.pack(fill='x', pady=(14, 6))
        ttk.Label(update_row, text='版本更新', font=(FONT, 13, 'bold')).pack(side='left')
        self.button(update_row, '检查更新', self.check, side='right')
        self.install_button = self.button(update_row, '下载并安装', self.install, 'Primary.TButton', side='right')
        self.install_button.configure(state='disabled')
        self.release_text = tk.StringVar(value='点击检查更新，获取 GitHub 上发布的最新正式版本。')
        ttk.Label(outer, textvariable=self.release_text, wraplength=700).pack(anchor='w', pady=(4, 10))
        self.notes = tk.Text(outer, height=8, relief='flat', bg='white', fg='#42594c', wrap='word',
                             padx=14, pady=12, font=(FONT, 10))
        self.notes.pack(fill='both', expand=True)
        self.set_notes('日常使用\n\n启动后会自动打开浏览器。关闭启动器不会停止正在运行的系统。\n\n更新会保留本地模型配置、文案数据库和图片素材。')
        self.progress = ttk.Progressbar(outer, mode='indeterminate')
        self.progress.pack(fill='x', pady=(16, 8))
        self.message = tk.StringVar(value='准备就绪')
        ttk.Label(outer, textvariable=self.message, wraplength=700, foreground='#607366').pack(anchor='w')
        bottom = ttk.Frame(outer); bottom.pack(fill='x', pady=(16, 0))
        self.button(bottom, '更新源与端口', self.settings)
        self.button(bottom, '打开日志', self.logs)
        self.button(bottom, '发布页面', lambda: webbrowser.open('https://github.com/' + core.config(ROOT)['repository'] + '/releases'), side='right')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.drain)
        self.after(200, self.initialize)

    def button(self, parent, text, command, style='TButton', side='left'):
        button = ttk.Button(parent, text=text, command=command, style=style)
        button.pack(side=side, padx=(0, 8))
        self.buttons.append(button)
        return button

    def set_notes(self, text):
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', text)
        self.notes.configure(state='disabled')

    def progress_message(self, text):
        self.events.put(('message', text))

    def run(self, operation, callback=None):
        if self.working:
            return
        self.working = True
        for button in self.buttons:
            button.configure(state='disabled')
        self.progress.start(12)
        def work():
            try:
                value = operation()
                self.events.put(('success', (callback, value)))
            except Exception as exc:
                log = ROOT / '.launcher/launcher.log'
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open('a', encoding='utf-8') as f:
                    f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + traceback.format_exc() + '\n')
                message = str(exc) if isinstance(exc, core.LauncherError) else '操作未完成，请检查网络或查看日志后重试。'
                self.events.put(('error', message))
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        while not self.events.empty():
            event, value = self.events.get_nowait()
            if event == 'message':
                self.message.set(value)
                continue
            self.working = False
            self.progress.stop()
            for button in self.buttons:
                button.configure(state='normal')
            if not self.update_info:
                self.install_button.configure(state='disabled')
            if event == 'error':
                self.message.set(value)
                messagebox.showerror('操作未完成', value, parent=self)
            else:
                callback, result = value
                if callback:
                    callback(result)
        self.after(100, self.drain)

    def initialize(self):
        def operation():
            with core.installation_lock(ROOT):
                core.recover_interrupted(ROOT, self.progress_message)
            return core.control(ROOT)
        self.run(operation, self.show_state)

    def show_state(self, state):
        self.version.set('当前版本  ' + core.current_version(ROOT)['version'])
        self.state_text.set('服务运行中' if state else '服务未启动')
        self.message.set('准备就绪' if not state else '本地服务已就绪')

    def launch(self):
        def operation():
            with core.installation_lock(ROOT):
                core.recover_interrupted(ROOT, self.progress_message)
                return core.start_service(ROOT, progress=self.progress_message)
        self.run(operation, lambda value: (self.show_state(value), self.open_page()))

    def open_page(self):
        webbrowser.open('http://127.0.0.1:' + str(core.config(ROOT)['port']))

    def stop(self):
        def operation():
            with core.installation_lock(ROOT):
                core.stop_service(ROOT, self.progress_message)
        self.run(operation, lambda _: self.show_state(None))

    def check(self):
        def done(value):
            self.update_info = value
            if value:
                manifest = value['manifest']
                self.release_text.set('发现新版本 %s · 更新包 %.1f MB' % (manifest['version'], manifest['size'] / 1048576))
                self.set_notes(value['notes'] or '本次更新没有附带发布说明。')
                self.install_button.configure(state='normal')
                self.message.set('签名清单已验证，可以下载并安装')
            else:
                self.release_text.set('当前已是最新正式版本')
                self.install_button.configure(state='disabled')
                self.message.set('检查完成')
        self.run(lambda: core.check_update(ROOT, self.progress_message), done)

    def install(self):
        if not self.update_info:
            return
        version = self.update_info['manifest']['version']
        if not messagebox.askyesno('安装更新', f'安装 {version}？\n\n会先备份并短暂重启本地服务。配置、文案和素材保留；有任务运行时会等待你稍后重试。', parent=self):
            return
        def done(value):
            self.update_info = None
            self.version.set('当前版本  ' + value)
            self.install_button.configure(state='disabled')
            messagebox.showinfo('更新完成', '已更新至 ' + value + '。启动器将重新打开。', parent=self)
            self.relaunch()
        self.run(lambda: core.install_update(ROOT, self.update_info, self.progress_message), done)

    def relaunch(self):
        python = core.python_path(ROOT)
        if os.name == 'nt':
            python = python.with_name('pythonw.exe')
        env = os.environ.copy()
        if getattr(sys, 'frozen', False):
            env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        subprocess.Popen([str(python), '-s', str(ROOT / 'launcher/ui.py')], cwd=ROOT, env=env,
                         start_new_session=os.name != 'nt',
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.destroy()

    def settings(self):
        saved = core.config(ROOT)
        dialog = tk.Toplevel(self); dialog.title('更新源与端口'); dialog.geometry('580x285'); dialog.transient(self); dialog.grab_set()
        frame = ttk.Frame(dialog, padding=22); frame.pack(fill='both', expand=True)
        repository = tk.StringVar(value=saved['repository']); port = tk.StringVar(value=str(saved['port']))
        ttk.Label(frame, text='GitHub 公开更新仓库').pack(anchor='w')
        ttk.Entry(frame, textvariable=repository).pack(fill='x', pady=(6, 14))
        ttk.Label(frame, text='本地服务端口（停止服务后可修改）').pack(anchor='w')
        ttk.Entry(frame, textvariable=port).pack(fill='x', pady=(6, 14))
        def save():
            try:
                value = {'repository': core.repository_name(repository.get()), 'port': int(port.get())}
                if not 1024 <= value['port'] <= 65535:
                    raise ValueError()
                with core.installation_lock(ROOT):
                    if value['port'] != saved['port'] and core.control(ROOT):
                        raise core.LauncherError('请先停止当前服务，再修改端口')
                    core.atomic_json(ROOT / 'launcher-config.json', value)
                self.update_info = None; self.install_button.configure(state='disabled')
                dialog.destroy(); self.message.set('设置已保存')
            except (core.LauncherError, ValueError) as exc:
                messagebox.showerror('设置未保存', str(exc) or '端口应为 1024–65535 的整数', parent=dialog)
        ttk.Button(frame, text='保存设置', command=save).pack(anchor='e')

    def logs(self):
        path = ROOT / '.launcher'; path.mkdir(exist_ok=True)
        if sys.platform == 'darwin':
            subprocess.Popen(['/usr/bin/open', str(path)])
        else:
            os.startfile(path)

    def close(self):
        if self.working:
            messagebox.showinfo('操作进行中', '请等待当前操作完成后再关闭启动器。', parent=self)
            return
        self.destroy()


if __name__ == '__main__':
    if '--smoke' in sys.argv:
        window = Launcher()
        window.after(1400, window.destroy)
        window.mainloop()
    else:
        Launcher().mainloop()
