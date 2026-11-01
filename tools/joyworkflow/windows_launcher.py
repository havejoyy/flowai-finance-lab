"""Portable Windows controller: bundled engine + browser UI, no host Python."""
import json,os,subprocess,sys,time,webbrowser
from pathlib import Path
from desktop_backend import existing_session

def engine_command(root):
    prefix=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]
    return prefix+['--engine','--data-dir',str(root),'--port','0']

def request_stop(root,state):
    current=existing_session(root)
    if not current or current['instance']!=state['instance']:return False
    temporary=root/'desktop-stop.tmp';temporary.write_text(json.dumps({'instance':state['instance']}),encoding='utf-8');temporary.replace(root/'desktop-stop.json');return True

class Launcher:
    def __init__(self,window,root):
        import tkinter as tk
        from tkinter import ttk
        self.window=window;self.root=Path(root).expanduser().resolve();self.process=None;self.state=None;self.started=time.monotonic();self.closing=False
        self.root.mkdir(parents=True,exist_ok=True)
        window.title('JoyWorkflow · 本地策略研究');window.geometry('580x290');window.minsize(540,270)
        style=ttk.Style();style.theme_use('vista' if 'vista' in style.theme_names() else 'clam');style.configure('TLabel',font=('Segoe UI',11));style.configure('TButton',font=('Segoe UI',10),padding=8)
        frame=ttk.Frame(window,padding=24);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='JoyWorkflow',font=('Segoe UI',20,'bold')).pack(anchor='w')
        self.status=tk.StringVar(value='正在启动本地数据服务…');ttk.Label(frame,textvariable=self.status,wraplength=510).pack(anchor='w',pady=(12,8))
        ttk.Label(frame,text='关闭浏览器不会停止服务；退出此窗口才会停止。',font=('Segoe UI',10)).pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(20,8))
        self.open_button=ttk.Button(row,text='打开研究页面',command=self.open_page,state='disabled');self.open_button.pack(side='left',padx=(0,8))
        ttk.Button(row,text='数据文件夹',command=lambda:self.open_file(self.root)).pack(side='left',padx=(0,8))
        ttk.Button(row,text='查看日志',command=lambda:self.open_file(self.root/'desktop.log')).pack(side='left')
        ttk.Button(row,text='退出',command=self.close).pack(side='right')
        window.protocol('WM_DELETE_WINDOW',self.close)
        self.state=existing_session(self.root)
        if not self.state:
            env=dict(os.environ,PYINSTALLER_RESET_ENVIRONMENT='1',PYTHONIOENCODING='utf-8')
            self.process=subprocess.Popen(engine_command(self.root),env=env,stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        window.after(100,self.poll)
    def open_file(self,path):
        if sys.platform=='win32':os.startfile(str(path))
    def open_page(self):
        if self.state:webbrowser.open(self.state['url']+'/screen')
    def poll(self):
        if self.closing:return
        state=existing_session(self.root)
        if state:
            self.state=state;self.status.set('本地服务已就绪，数据保存于：\n'+str(self.root));self.open_button.configure(state='normal');self.open_page();return
        if (self.process and self.process.poll() is not None) or time.monotonic()-self.started>60:
            from tkinter import messagebox
            self.status.set('服务启动失败。原有数据保留，可查看日志后重新打开。')
            messagebox.showerror('启动遇到问题','本地服务未能启动。请检查数据目录权限和磁盘空间，查看 desktop.log 后重新打开。',parent=self.window);return
        self.window.after(250,self.poll)
    def close(self):
        if self.closing:return
        from tkinter import messagebox
        if self.process and self.state:
            try:
                from urllib.request import build_opener,ProxyHandler
                with build_opener(ProxyHandler({})).open(self.state['url']+'/api/desktop-health',timeout=2) as r:busy=json.load(r).get('busy')
                if busy and not messagebox.askyesno('筛选仍在运行','退出会中断本轮筛选，已保存记录保留。确认退出？',default='no',parent=self.window):return
            except OSError:pass
            request_stop(self.root,self.state)
        self.closing=True;self.deadline=time.monotonic()+10;self.status.set('正在停止本地服务…');self.window.after(100,self.wait_stop)
    def wait_stop(self):
        if self.process and self.process.poll() is None:
            if time.monotonic()<self.deadline:self.window.after(100,self.wait_stop);return
            self.process.terminate()
        self.window.destroy()

def main():
    if '--engine' in sys.argv or '--self-check' in sys.argv:
        if '--engine' in sys.argv:sys.argv.remove('--engine')
        import app
        data=Path(sys.argv[sys.argv.index('--data-dir')+1]) if '--data-dir' in sys.argv else app.default_root()
        data.mkdir(parents=True,exist_ok=True)
        log=(data/'desktop.log').open('a',encoding='utf-8',buffering=1)
        sys.stdout=sys.stderr=log
        try:
            from desktop_backend import main as run_engine
            return run_engine()
        except BaseException:
            import traceback
            traceback.print_exc();raise
    import argparse,tkinter as tk
    from tkinter import messagebox
    import app
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,default=app.default_root());args=parser.parse_args()
    if sys.platform=='win32':
        import ctypes
        try:ctypes.windll.user32.SetProcessDPIAware()
        except OSError:pass
    window=tk.Tk()
    try:Launcher(window,args.data_dir)
    except Exception as exc:messagebox.showerror('JoyWorkflow 启动失败','无法启动，请检查数据目录权限与磁盘空间。\n'+type(exc).__name__,parent=window);window.destroy();return 1
    window.mainloop();return 0
if __name__=='__main__':sys.exit(main())
