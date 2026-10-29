"""Native launcher; all network and service work stays off the UI thread."""
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
BG, CARD, INK, MUTED, GREEN = '#f3f5f1', '#ffffff', '#203c34', '#687a71', '#245c4d'


class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('XHS Agent · 内容工作台')
        self.geometry('920x700')
        self.minsize(900, 700)
        self.configure(bg=BG)
        self.events = queue.Queue()
        self.working = False
        self.running = False
        self.ready = False
        self.update_info = None
        self.release_notes = ''
        self.buttons = []
        self._timers = []
        self._closed = False
        self._closing = False
        self._styles()
        outer = tk.Frame(self, bg=BG, padx=30, pady=26)
        outer.pack(fill='both', expand=True)
        header = tk.Frame(outer, bg=BG)
        header.pack(fill='x', pady=(0, 26))
        mark = tk.Canvas(header, width=48, height=48, bg=BG, highlightthickness=0)
        mark.pack(side='left', padx=(0, 14))
        mark.create_oval(1, 1, 47, 47, fill=GREEN, outline='')
        mark.create_line(16, 15, 32, 33, fill='white', width=3)
        mark.create_line(32, 15, 16, 33, fill='white', width=3)
        identity = tk.Frame(header, bg=BG); identity.pack(side='left')
        self.label(identity, 'XHS Agent', size=22, bold=True).pack(anchor='w')
        self.label(identity, '小红书 AI 内容工作台', color=MUTED).pack(anchor='w', pady=(2, 0))
        self.version = tk.StringVar(value='v' + core.current_version(ROOT)['version'])
        self.label(header, variable=self.version, color=MUTED).pack(side='right')

        cards = tk.Frame(outer, bg=BG)
        cards.columnconfigure(0, weight=3, uniform='cards')
        cards.columnconfigure(1, weight=2, uniform='cards')
        cards.rowconfigure(0, weight=1)
        main = self.card(cards); main.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        update = self.card(cards); update.grid(row=0, column=1, sticky='nsew')
        self.state_text = tk.StringVar(value='正在检查本地服务…')
        self.state_badge = self.label(main, variable=self.state_text, color=MUTED, size=10)
        self.state_badge.pack(anchor='w')
        self.hero_title = tk.StringVar(value='准备你的创作空间')
        self.label(main, variable=self.hero_title, size=23, bold=True).pack(anchor='w', pady=(22, 10))
        self.hero_detail = tk.StringVar(value='正在确认服务状态，请稍候。')
        self.label(main, variable=self.hero_detail, color=MUTED, wrap=410).pack(anchor='w')
        features = tk.Frame(main, bg='#f2f6f2', padx=16, pady=14)
        features.pack(fill='x', pady=(18, 18))
        self.label(features, '选题灵感  /  文案生成  /  数据复盘', size=10, color=GREEN).pack(anchor='w')
        self.label(features, '项目、素材与模型配置都会保留。', size=10, color=MUTED).pack(anchor='w', pady=(7, 0))
        actions = tk.Frame(main, bg=CARD); actions.pack(side='bottom', fill='x')
        self.launch_button = self.button(actions, '启动工作台', self.launch, 'Primary.TButton')
        self.launch_button.pack(fill='x', pady=(0, 10))
        self.stop_button = self.button(actions, '停止服务', self.stop, 'Quiet.TButton')
        self.stop_button.pack(anchor='w')

        self.label(update, '版本与更新', size=15, bold=True).pack(anchor='w')
        self.label(update, '保持工作台常新', size=10, color=MUTED).pack(anchor='w', pady=(5, 24))
        self.release_title = tk.StringVar(value='当前版本 ' + core.current_version(ROOT)['version'])
        self.label(update, variable=self.release_title, size=17, bold=True).pack(anchor='w')
        self.release_text = tk.StringVar(value='检查是否有新功能和问题修复。')
        self.label(update, variable=self.release_text, color=MUTED, wrap=260).pack(anchor='w', pady=(10, 12))
        self.notes_button = self.button(update, '查看更新说明  →', self.show_notes, 'Quiet.TButton')
        self.notes_button.pack(anchor='w')
        update_actions = tk.Frame(update, bg=CARD); update_actions.pack(side='bottom', fill='x')
        self.install_button = self.button(update_actions, '下载并安装', self.install, 'Primary.TButton')
        self.install_button.pack(fill='x', pady=(0, 10))
        self.check_button = self.button(update_actions, '检查更新', self.check)
        self.check_button.pack(fill='x')

        footer = tk.Frame(outer, bg=BG); footer.pack(side='bottom', fill='x', pady=(18, 10))
        for text, command in (('启动器设置', self.settings), ('运行日志', self.logs), ('版本发布页', self.release_page)):
            self.button(footer, text, command, 'Footer.TButton').pack(side='left', padx=(0, 10))
        self.label(footer, '关闭启动器时，自动停止本服务', color=MUTED, size=9).pack(side='right')
        status = tk.Frame(outer, bg='#e9eee8', padx=14, pady=10)
        status.pack(side='bottom', fill='x', before=footer)
        self.message = tk.StringVar(value='正在初始化…')
        self.message_label = self.label(status, variable=self.message, color=MUTED, size=10, wrap=780)
        self.message_label.pack(anchor='w')
        self.progress = ttk.Progressbar(status, mode='indeterminate', style='Green.Horizontal.TProgressbar')
        cards.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        if sys.platform == 'darwin':
            # Cmd+Q / application-menu Quit must follow the same shutdown path.
            self.createcommand('tk::mac::Quit', self.close)
        self.sync_controls()
        self._later(100, self.drain)
        self._later(200, self.initialize)

    def _styles(self):
        style = ttk.Style(self); style.theme_use('clam')
        style.configure('TButton', font=(FONT, 11), padding=(15, 11), background='#edf2ed',
                        foreground=INK, borderwidth=0, focusthickness=2, focuscolor=GREEN)
        style.map('TButton', background=[('disabled', '#f1f3ef'), ('pressed', '#dce5dc'), ('active', '#e2eae1')],
                  foreground=[('disabled', '#9ca99f')])
        style.configure('Primary.TButton', font=(FONT, 12, 'bold'), padding=(18, 13), background=GREEN, foreground='white')
        style.map('Primary.TButton', background=[('disabled', '#dce5dc'), ('pressed', '#194638'), ('active', '#33705d')],
                  foreground=[('disabled', '#87998a')])
        style.configure('Quiet.TButton', background=CARD, padding=(2, 5), foreground=GREEN)
        style.map('Quiet.TButton', background=[('disabled', CARD), ('active', '#eef4ee')])
        style.configure('Footer.TButton', background=BG, padding=(2, 7), foreground=MUTED, font=(FONT, 10))
        style.map('Footer.TButton', background=[('disabled', BG), ('active', '#e4ebe2')])
        style.configure('Green.Horizontal.TProgressbar', background=GREEN, troughcolor='#dbe5da', borderwidth=0, thickness=3)

    def label(self, parent, text='', variable=None, size=11, bold=False, color=INK, wrap=0):
        return tk.Label(parent, text=text, textvariable=variable, bg=parent.cget('bg'), fg=color,
                        font=(FONT, size, 'bold' if bold else 'normal'), anchor='w', justify='left', wraplength=wrap)

    def card(self, parent):
        return tk.Frame(parent, bg=CARD, padx=26, pady=24, highlightbackground='#dfe6dc', highlightthickness=1)

    def button(self, parent, text, command, style='TButton'):
        button = ttk.Button(parent, text=text, command=command, style=style)
        self.buttons.append(button)
        return button

    def _later(self, delay, callback):
        token = None
        def invoke():
            self._timers.remove(token)
            callback()
        token = self.after(delay, invoke)
        self._timers.append(token)

    def destroy(self):
        self._closed = True
        for token in self._timers:
            self.after_cancel(token)
        self._timers.clear()
        super().destroy()

    def sync_controls(self):
        for button in self.buttons:
            button.configure(state='disabled' if self.working else 'normal')
        for button, enabled in ((self.launch_button, self.ready), (self.stop_button, self.ready and self.running),
                                (self.install_button, bool(self.update_info)), (self.notes_button, bool(self.release_notes))):
            button.configure(state='normal' if enabled and not self.working else 'disabled')
        self.launch_button.configure(text='打开工作台  →' if self.running else '启动工作台  →')

    def set_notes(self, text):
        self.release_notes = text

    def show_notes(self):
        dialog = tk.Toplevel(self); dialog.title('更新说明'); dialog.geometry('620x450'); dialog.transient(self)
        frame = tk.Frame(dialog, bg=CARD, padx=24, pady=24); frame.pack(fill='both', expand=True)
        self.label(frame, self.release_title.get(), size=18, bold=True).pack(anchor='w', pady=(0, 16))
        scroll = ttk.Scrollbar(frame); scroll.pack(side='right', fill='y')
        notes = tk.Text(frame, relief='flat', wrap='word', bg=CARD, fg=INK, font=(FONT, 11), yscrollcommand=scroll.set)
        notes.insert('1.0', self.release_notes); notes.configure(state='disabled'); notes.pack(fill='both', expand=True)
        scroll.configure(command=notes.yview)

    def progress_message(self, text):
        self.events.put(('message', text))

    def run(self, operation, callback=None, message='正在处理，请稍候…'):
        if self.working:
            return
        self.working = True
        self.message.set(message); self.message_label.configure(fg=MUTED)
        self.sync_controls()
        self.progress.pack(fill='x', pady=(9, 0)); self.progress.start(12)
        def work():
            try:
                self.events.put(('success', (callback, operation())))
            except Exception as exc:
                try:
                    log = ROOT / '.launcher/launcher.log'; log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open('a', encoding='utf-8') as stream:
                        stream.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + traceback.format_exc() + '\n')
                except OSError:
                    pass
                detail = str(exc) if isinstance(exc, core.LauncherError) else '操作未完成，请检查网络或查看运行日志后重试。'
                self.events.put(('error', detail))
        threading.Thread(target=work, daemon=True).start()

    def drain(self):
        while not self.events.empty():
            event, value = self.events.get_nowait()
            if event == 'message':
                self.message.set(value)
                continue
            if event == 'check_failed':
                self.release_title.set('暂时无法检查更新')
                self.release_text.set('请检查网络后，点击「检查更新」重试。')
                continue
            self.working = False
            self.progress.stop(); self.progress.pack_forget()
            if event == 'error':
                self._closing = False
                self.message.set(value); self.message_label.configure(fg='#a14731')
                if not self.ready:
                    self.ready = True
                    self.hero_detail.set('初始化未完成。点击启动可重试，详情见运行日志。')
                messagebox.showerror('操作未完成', value, parent=self)
            else:
                callback, result = value
                if callback:
                    callback(result)
                    if self._closed:
                        return
            self.sync_controls()
        self._later(100, self.drain)

    def initialize(self):
        def operation():
            with core.installation_lock(ROOT):
                core.recover_interrupted(ROOT, self.progress_message)
            return core.control(ROOT)
        self.run(operation, self.show_state, '正在检查本地服务…')

    def show_state(self, state):
        self.ready = True; self.running = bool(state)
        self.version.set('v' + core.current_version(ROOT)['version'])
        self.state_text.set('服务运行中' if state else '服务未启动')
        self.state_badge.configure(fg=GREEN if state else MUTED)
        self.hero_title.set('工作台已就绪' if state else '开始今天的创作')
        self.hero_detail.set('点击下方按钮，回到浏览器中的内容工作台。' if state else '一键启动本地服务，并在浏览器中打开工作台。')
        self.message.set('本地服务已就绪 · 关闭启动器会自动停止服务' if state else '准备就绪 · 点击「启动工作台」开始使用')
        self.sync_controls()

    def launch(self):
        # Re-check readiness when opening: another launcher may have stopped the service.
        def operation():
            with core.installation_lock(ROOT):
                core.recover_interrupted(ROOT, self.progress_message)
                return core.start_service(ROOT, progress=self.progress_message)
        self.run(operation, lambda value: (self.show_state(value), self.open_page()), '正在打开工作台…' if self.running else '正在启动工作台，首次启动可能需要稍等…')

    def open_page(self):
        webbrowser.open('http://127.0.0.1:' + str(core.config(ROOT)['port']))

    def stop(self):
        def operation():
            with core.installation_lock(ROOT):
                core.stop_service(ROOT, self.progress_message)
        self.run(operation, lambda _: self.show_state(None), '正在停止本地服务…')

    def check(self):
        if self.working:
            return
        self.update_info = None; self.release_notes = ''
        self.release_title.set('正在检查…')
        self.release_text.set('正在获取最新正式版本。')
        def done(value):
            self.update_info = value
            if value:
                manifest = value['manifest']
                self.release_title.set('新版本 ' + manifest['version'])
                self.release_text.set('更新包 %.1f MB\n安装后保留配置、文案和素材。' % (manifest['size'] / 1048576))
                self.set_notes(value['notes'] or '本次更新没有附带发布说明。')
                self.message.set('发现新版本，签名已验证 · 可以下载并安装')
            else:
                self.release_title.set('已是最新版本')
                self.release_text.set('当前版本 ' + core.current_version(ROOT)['version'] + '\n你已拥有最新的功能与修复。')
                self.message.set('检查完成 · 无需更新')
        def operation():
            try:
                return core.check_update(ROOT, self.progress_message)
            except Exception:
                self.events.put(('check_failed', None))
                raise
        self.run(operation, done, '正在检查最新版本…')

    def install(self):
        if not self.update_info or self.working:
            return
        info = self.update_info
        version = info['manifest']['version']
        if not messagebox.askyesno('安装更新', f'安装 {version}？\n\n会先备份并短暂重启本地服务。配置、文案和素材保留；有任务运行时请稍后重试。', parent=self):
            return
        def done(value):
            self.update_info = None
            self.version.set('v' + value)
            messagebox.showinfo('更新完成', '已更新至 ' + value + '。启动器将重新打开。', parent=self)
            self.relaunch()
        self.run(lambda: core.install_update(ROOT, info, self.progress_message), done, '正在准备更新，请保持启动器打开…')

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
        dialog = tk.Toplevel(self); dialog.title('启动器设置'); dialog.geometry('580x420'); dialog.minsize(580, 420); dialog.transient(self); dialog.grab_set()
        frame = tk.Frame(dialog, bg=CARD, padx=24, pady=24); frame.pack(fill='both', expand=True)
        repository = tk.StringVar(value=saved['repository']); port = tk.StringVar(value=str(saved['port']))
        self.label(frame, '更新与连接', size=17, bold=True).pack(anchor='w', pady=(0, 18))
        self.label(frame, 'GitHub 公开更新仓库', size=10).pack(anchor='w')
        ttk.Entry(frame, textvariable=repository, font=(FONT, 11)).pack(fill='x', pady=(6, 14))
        self.label(frame, '本地服务端口（停止服务后可修改）', size=10).pack(anchor='w')
        ttk.Entry(frame, textvariable=port, font=(FONT, 11)).pack(fill='x', pady=(6, 12))
        self.label(frame, 'API 与模型请在网页「模型与访问设置」中修改。', size=10, color=MUTED).pack(anchor='w')
        def save():
            try:
                value = {'repository': core.repository_name(repository.get()), 'port': int(port.get())}
                if not 1024 <= value['port'] <= 65535:
                    raise ValueError()
                with core.installation_lock(ROOT):
                    if value['port'] != saved['port'] and core.control(ROOT):
                        raise core.LauncherError('请先停止当前服务，再修改端口')
                    core.atomic_json(ROOT / 'launcher-config.json', value)
                self.update_info = None; self.release_notes = ''
                self.release_title.set('当前版本 ' + core.current_version(ROOT)['version'])
                self.release_text.set('设置已变更，请重新检查更新。')
                self.sync_controls(); dialog.destroy(); self.message.set('设置已保存')
            except (core.LauncherError, ValueError) as exc:
                messagebox.showerror('设置未保存', str(exc) or '端口应为 1024–65535 的整数', parent=dialog)
        ttk.Button(frame, text='保存设置', command=save, style='Primary.TButton').pack(anchor='e', pady=(16, 0))

    def release_page(self):
        webbrowser.open('https://github.com/' + core.config(ROOT)['repository'] + '/releases')

    def logs(self):
        path = ROOT / '.launcher'; path.mkdir(exist_ok=True)
        if sys.platform == 'darwin':
            subprocess.Popen(['/usr/bin/open', str(path)])
        else:
            os.startfile(path)

    def close(self):
        if self._closing:
            return
        if self.working:
            messagebox.showinfo('操作进行中', '请等待当前操作完成后再关闭启动器。', parent=self)
            return
        self._closing = True
        def operation():
            with core.installation_lock(ROOT):
                try:
                    # Recheck the authenticated service, including a service
                    # started by another launcher for this same installation.
                    core.stop_service(ROOT, self.progress_message)
                except core.ServiceConflict:
                    return '本启动器将退出。当前端口属于其他程序或另一份安装，未停止它；请使用对应目录的启动器停止原服务。'
            return None
        def done(notice):
            if notice:
                messagebox.showinfo('未操作其他服务', notice, parent=self)
            self.destroy()
        self.run(operation, done, '正在停止后台服务和任务进程，完成后自动退出…')


if __name__ == '__main__':
    window = Launcher()
    if '--smoke' in sys.argv:
        window.after(1400, window.destroy)
    window.mainloop()
