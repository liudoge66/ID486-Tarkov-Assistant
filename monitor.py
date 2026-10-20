import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
import winsound
from collections import deque
from detector import Detector, Gate, ExtractionGuard
from diagnostics import Diagnostics
APP_BUILD='2026.10.21-v0.1.3-exit-observation'

U=C.WinDLL('user32',use_last_error=True)
K=C.WinDLL('kernel32',use_last_error=True)
U.GetForegroundWindow.restype=W.HWND
U.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
U.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
U.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
K.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];K.OpenProcess.restype=W.HANDLE
K.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
K.CloseHandle.argtypes=[W.HANDLE]
K.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];K.WaitForSingleObject.restype=W.DWORD
K.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR];K.CreateMutexW.restype=W.HANDLE
U.RegisterHotKey.argtypes=[W.HWND,C.c_int,W.UINT,W.UINT]
U.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
U.GetMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT]
U.GetMessageW.restype=W.BOOL
U.PostThreadMessageW.argtypes=[W.DWORD,W.UINT,W.WPARAM,W.LPARAM]
try: U.SetProcessDpiAwarenessContext(C.c_void_p(-4))
except Exception: pass

class MOUSEINPUT(C.Structure):
    _fields_=[('dx',W.LONG),('dy',W.LONG),('mouseData',W.DWORD),('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]
class KEYBDINPUT(C.Structure):
    _fields_=[('wVk',W.WORD),('wScan',W.WORD),('dwFlags',W.DWORD),('time',W.DWORD),('dwExtraInfo',C.c_size_t)]
class HARDWAREINPUT(C.Structure):
    _fields_=[('uMsg',W.DWORD),('wParamL',W.WORD),('wParamH',W.WORD)]
class INPUTUNION(C.Union):
    _fields_=[('mi',MOUSEINPUT),('ki',KEYBDINPUT),('hi',HARDWAREINPUT)]
class INPUT(C.Structure):
    _anonymous_=('u',)
    _fields_=[('type',W.DWORD),('u',INPUTUNION)]
U.SendInput.argtypes=[W.UINT,C.POINTER(INPUT),C.c_int];U.SendInput.restype=W.UINT

class NOTIFYICONDATA(C.Structure):
    _fields_=[('cbSize',W.DWORD),('hWnd',W.HWND),('uID',W.UINT),('uFlags',W.UINT),
              ('uCallbackMessage',W.UINT),('hIcon',W.HICON),('szTip',W.WCHAR*128),
              ('dwState',W.DWORD),('dwStateMask',W.DWORD),('szInfo',W.WCHAR*256),
              ('uTimeoutOrVersion',W.UINT),('szInfoTitle',W.WCHAR*64),('dwInfoFlags',W.DWORD),
              ('guidItem',C.c_byte*16),('hBalloonIcon',W.HICON)]

class WindowsNotice:
    def __init__(self, root, key_name):
        root.update_idletasks()
        U.GetAncestor.argtypes=[W.HWND,W.UINT];U.GetAncestor.restype=W.HWND
        U.LoadIconW.argtypes=[W.HINSTANCE,C.c_void_p];U.LoadIconW.restype=W.HICON
        self.shell=C.WinDLL('shell32',use_last_error=True)
        self.shell.Shell_NotifyIconW.argtypes=[W.DWORD,C.POINTER(NOTIFYICONDATA)]
        self.data=NOTIFYICONDATA();self.data.cbSize=C.sizeof(self.data)
        self.data.hWnd=U.GetAncestor(root.winfo_id(),2);self.data.uID=1
        self.data.uFlags=2|4;self.data.hIcon=U.LoadIconW(None,C.c_void_p(32516))
        self.data.szTip=f'ID486模拟助手 · {key_name} 开启/关闭'
        self.return_sound_enabled=True
        U.LoadImageW.argtypes=[W.HINSTANCE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT]
        U.LoadImageW.restype=W.HANDLE
        U.DestroyIcon.argtypes=[W.HICON]
        self.owned_icon=U.LoadImageW(None,str(Path(__file__).parent/'assets'/'subaru_clean.ico'),1,32,32,0x10)
        if self.owned_icon:self.data.hIcon=self.owned_icon
        self.available=bool(self.shell.Shell_NotifyIconW(0,C.byref(self.data)))

    def show(self, text):
        # Audio is independent of Windows notification suppression. Async playback
        # never delays the capture worker or takes focus from the game.
        enabled=text.startswith('监控中')
        actual_exit='已发送一次 Alt+F4' in text
        filename='return_by_death.wav' if actual_exit and self.return_sound_enabled else ('enabled.wav' if enabled else 'disabled.wav')
        sound=Path(__file__).parent/'assets'/filename
        audio_ok=False
        if '--smoke-test' not in sys.argv:
            try:
                winsound.PlaySound(str(sound),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
                audio_ok=True
            except RuntimeError:pass
        popup_ok=False
        if self.available:
            self.data.uFlags=0x10
            self.data.szInfoTitle='ID486模拟助手 · 监控已开启' if enabled else 'ID486模拟助手 · 已停止 / 未开启'
            self.data.szInfo=text[:255];self.data.dwInfoFlags=1
            popup_ok=bool(self.shell.Shell_NotifyIconW(1,C.byref(self.data)))
        state=C.c_int(-1)
        self.shell.SHQueryUserNotificationState(C.byref(state))
        return dict(enabled=enabled,audio_requested=audio_ok,popup_accepted=popup_ok,windows_notification_state=state.value)

    def close(self):
        if self.available:self.shell.Shell_NotifyIconW(2,C.byref(self.data))
        if self.owned_icon:U.DestroyIcon(self.owned_icon)

class Hotkeys:
    """Own registration AND message retrieval on a dedicated native thread.

    Tk consumes thread WM_HOTKEY messages before an after/PeekMessage poll can
    reliably see them. Never register on the Tk thread.
    """
    def __init__(self, events):
        self.events=events;self.registered=[];self.key_name='F10'
        self.ready=threading.Event();self.thread_id=None
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
        if not self.ready.wait(3):raise RuntimeError('快捷键线程启动超时')

    def run(self):
        self.thread_id=K.GetCurrentThreadId()
        msg=W.MSG()
        U.PeekMessageW(C.byref(msg),None,0,0,0)  # establish native message queue
        try:
            for name,key in (('F10',0x79),):
                if U.RegisterHotKey(None,1,0x4000,key):
                    self.registered.append(1);self.key_name=name;break
            self.ready.set()
            if not self.registered:return
            while U.GetMessageW(C.byref(msg),None,0,0)>0:
                if msg.message==0x312 and msg.wParam==1:
                    self.events.put(('hotkey',self.key_name))
        finally:
            for ident in self.registered:U.UnregisterHotKey(None,ident)

    def close(self):
        if self.thread_id:U.PostThreadMessageW(self.thread_id,0x12,0,0)
        self.thread.join(timeout=1)

def target():
    hwnd=U.GetForegroundWindow()
    if not hwnd:return None
    pid=W.DWORD();U.GetWindowThreadProcessId(hwnd,C.byref(pid))
    handle=K.OpenProcess(0x1000,False,pid.value)
    if not handle:return None
    try:
        buf=C.create_unicode_buffer(32768);size=W.DWORD(len(buf))
        if not K.QueryFullProcessImageNameW(handle,0,buf,C.byref(size)):return None
        if Path(buf.value).name.lower()!='escapefromtarkov.exe':return None
    finally:K.CloseHandle(handle)
    rect=W.RECT();origin=W.POINT(0,0)
    if not U.GetClientRect(hwnd,C.byref(rect)) or not U.ClientToScreen(hwnd,C.byref(origin)):return None
    width,height=U.GetSystemMetrics(0),U.GetSystemMetrics(1)
    # A fixed profile is deliberately restricted to primary fullscreen/borderless.
    if (origin.x,origin.y,rect.right,rect.bottom)!=(0,0,width,height):return None
    return (int(hwnd),int(pid.value),width,height)

def send_alt_f4(expected=None, report=None):
    # One attempt only. Never kill a process or retry an uncertain send.
    if any(U.GetAsyncKeyState(k)&0x8000 for k in (0x10,0x11,0x12,0x5B,0x5C)):
        return '未发送：Shift/Ctrl/Alt/Win 正被按住'
    if expected is not None and target()!=expected:return '未发送：游戏已失去前台'
    def packet(flags):
        return (INPUT*2)(*[INPUT(type=1,u=INPUTUNION(ki=KEYBDINPUT(v,0,flags,0,0))) for v in ((0x12,0x73) if flags==0 else (0x73,0x12))])
    began=time.perf_counter();C.set_last_error(0)
    down=U.SendInput(2,packet(0),C.sizeof(INPUT));down_error=C.get_last_error()
    try:
        if down==2:time.sleep(.040)
    finally:
        # Always release this chord, including partial sends and focus changes.
        C.set_last_error(0)
        up=U.SendInput(2,packet(2),C.sizeof(INPUT));up_error=C.get_last_error()
    if report is not None:report.update(down_count=down,up_count=up,down_error=down_error,up_error=up_error,
        hold_ms=40,elapsed_ms=round((time.perf_counter()-began)*1000,2),foreground_after=int(U.GetForegroundWindow() or 0))
    if down!=2 or up!=2:
        return f'输入未完整发送（按下 {down}/2，松开 {up}/2），已停止，不重试'
    return '已发送一次 Alt+F4；是否退出/回档需自行确认'

class SquareToggle(tk.Frame):
    """Large filled/outline square, with no tiny platform check glyph."""
    def __init__(self,parent,text,variable,command=None,bg='#FFFFFF'):
        super().__init__(parent,bg=bg)
        self.variable=variable;self.command=command;self.bg=bg
        self.box=tk.Canvas(self,width=28,height=28,bg=bg,highlightthickness=0,takefocus=1,bd=0)
        self.box.pack(side='left')
        self.label=tk.Label(self,text=text,bg=bg,fg='#322D4A',font=('Microsoft YaHei UI',10),cursor='hand2')
        self.label.pack(side='left',padx=(7,0))
        for widget in (self.box,self.label):widget.bind('<Button-1>',self.toggle)
        self.box.bind('<space>',self.toggle);self.box.bind('<Return>',self.toggle)
        self.variable.trace_add('write',self.redraw)
        self.redraw()

    def redraw(self,*args):
        self.box.delete('all')
        self.box.create_rectangle(4,4,25,25,outline='#8261CC',width=2,fill='#8261CC' if self.variable.get() else self.bg)

    def toggle(self,event=None):
        self.variable.set(not self.variable.get());self.box.focus_set()
        if self.command:self.command()
        return 'break'

class App:
    def __init__(self):
        self.root=tk.Tk();self.root.title('ID486模拟助手');self.root.geometry('640x660');self.root.resizable(False,False)
        self.root.configure(bg='#F4F1FC')
        self.root.iconbitmap(str(Path(__file__).parent/'assets'/'subaru_clean.ico'))
        self.lock=threading.RLock();self.token=0;self.active=False;self.arming=False;self.closed=False
        self.pending=queue.Queue();self.ui=queue.Queue();self.gate=Gate();self.detector=Detector()
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
        try:
            self.log=open(base/'monitor_events.jsonl','a',encoding='utf-8',buffering=1)
        except OSError:
            base=Path(os.environ['LOCALAPPDATA'])/'TarkovHUDMonitor';base.mkdir(exist_ok=True)
            self.log=open(base/'monitor_events.jsonl','a',encoding='utf-8',buffering=1)
        self.hotkey_service=Hotkeys(self.ui)
        self.hotkeys=self.hotkey_service.registered
        self.key_name=self.hotkey_service.key_name
        self.event('startup',hotkey=self.key_name,registered=bool(self.hotkeys),build=APP_BUILD)
        self.status=tk.StringVar(value=f'已关闭 · 进入战局、HUD 可见后按 {self.key_name}')
        self.scores=tk.StringVar(value='等待开启')
        self.dry=tk.BooleanVar(value=True)
        self.collect_enabled=False
        self.collect_var=tk.BooleanVar(value=False)
        self.diagnostic_status=tk.StringVar(value='截图默认关闭；手动开启后仅候选附近保存，最多2组 / 32MB')
        self.diagnostics=Diagnostics(base/'diagnostics',self.event)
        self.config_path=base/'settings.json'
        try:
            configured=int(json.loads(self.config_path.read_text(encoding='utf-8'))['delay_ms'])
            if not 50<=configured<=2000:raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):configured=150
        self.gate.delay_ms=configured
        self.delay_text=tk.StringVar(value=str(configured))
        try:
            hz=int(json.loads(self.config_path.read_text(encoding='utf-8')).get('capture_hz',30))
            if hz not in (15,30,60):raise ValueError()
        except (OSError,ValueError,TypeError):hz=30
        self.capture_hz=hz
        self.hz_text=tk.StringVar(value=str(hz))
        self.build_ui()
        self.notice=WindowsNotice(self.root,self.key_name)
        self.notice.return_sound_enabled=self.return_sound.get()
        if not self.hotkeys:
            self.status.set('快捷键注册失败：F10 已被占用，请关闭冲突后重启')
        threading.Thread(target=self.worker,daemon=True).start()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(10,self.tick)

    def build_ui(self):
        bg='#F4F1FC';ink='#322D4A';muted='#827A98';purple='#8261CC';white='#FFFFFF'
        self.root.option_add('*Font',('Microsoft YaHei UI',10))
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('R.TButton',background='#ECE5FA',foreground='#6343A1',borderwidth=0,padding=(12,8),font=('Microsoft YaHei UI',10))
        style.map('R.TButton',background=[('active','#DDCFF5')])
        style.configure('Stop.R.TButton',background='#8261CC',foreground='white')
        style.map('Stop.R.TButton',background=[('active','#6D4EB4')])
        style.configure('R.TCheckbutton',background=white,foreground=ink,indicatorcolor='#E7DEF6',padding=4)
        style.map('R.TCheckbutton',indicatorcolor=[('selected','#9C7BD9')],background=[('active',white)])
        style.configure('R.TCombobox',fieldbackground=white,foreground=ink,padding=4)
        self.state_head=tk.StringVar(value='待命 · 新的一轮')
        self.bottom_status=tk.StringVar(value='等待开启')
        self.return_sound=tk.BooleanVar(value=True)
        outer=tk.Frame(self.root,bg=bg);outer.pack(fill='both',expand=True,padx=24,pady=(18,14))
        footer=tk.Frame(outer,bg=bg);footer.pack(side='bottom',fill='x',pady=(12,0))
        self.bottom_label=tk.Label(footer,textvariable=self.bottom_status,bg=bg,fg=muted,font=('Microsoft YaHei UI',9),height=1,anchor='w')
        self.bottom_label.pack(fill='x')
        hero=tk.Frame(outer,bg=bg);hero.pack(fill='x')
        self.mascot_image=tk.PhotoImage(file=str(Path(__file__).parent/'assets'/'mascot_ui.png'))
        tk.Label(hero,image=self.mascot_image,bg=bg).pack(side='right',padx=(8,0))
        names=tk.Frame(hero,bg=bg);names.pack(side='left',fill='both',expand=True,pady=(12,8))
        tk.Label(names,text='ID486模拟助手',bg=bg,fg=ink,font=('Microsoft YaHei UI',27,'bold')).pack(anchor='w')
        tk.Label(names,text='RETURN BY DEATH / TARKOV',bg=bg,fg=purple,font=('Segoe UI',10,'bold')).pack(anchor='w',pady=(3,9))
        tk.Label(names,text='仅用于本地战局模拟大退 · 禁止线上使用',bg=bg,fg=muted,font=('Microsoft YaHei UI',9)).pack(anchor='w')
        card=tk.Frame(outer,bg=white,highlightbackground='#E6DFF2',highlightthickness=1,padx=18,pady=15)
        card.pack(fill='x',pady=(8,12))
        heading=tk.Frame(card,bg=white);heading.pack(fill='x')
        tk.Label(heading,textvariable=self.state_head,bg=white,fg=purple,font=('Microsoft YaHei UI',15,'bold')).pack(side='left')
        tk.Label(heading,text=' F10 ',bg='#EDE6FA',fg='#7051AF',font=('Segoe UI',11,'bold'),padx=10,pady=4).pack(side='right')
        tk.Label(card,textvariable=self.status,bg=white,fg=muted,font=('Microsoft YaHei UI',10),wraplength=540,justify='left').pack(anchor='w',pady=(12,10))
        tk.Label(card,text='回到游戏后按 F10 开启，再按一次停止',bg=white,fg=ink,font=('Microsoft YaHei UI',10)).pack(anchor='w')
        self.practice_toggle=SquareToggle(card,text='演练模式 · 只记录，不发送退出按键',variable=self.dry,
            command=lambda:self.cancel('模式已切换，需重新按 F10'))
        self.practice_toggle.pack(anchor='w',pady=(10,0))
        audio=tk.Frame(outer,bg=white,padx=13,pady=8);audio.pack(fill='x',pady=(0,10))
        self.audio_toggle=SquareToggle(audio,text='死亡回归音效 · 仅实际大退时播放',variable=self.return_sound,
            command=lambda:setattr(self.notice,'return_sound_enabled',self.return_sound.get()))
        self.audio_toggle.pack(anchor='w')
        preview_row=tk.Frame(audio,bg=white);preview_row.pack(fill='x',pady=(6,2))
        for label,name in (('试听开启','enabled.wav'),('试听关闭','disabled.wav'),('试听大退','return_by_death.wav')):
            ttk.Button(preview_row,text=label,style='R.TButton',command=lambda n=name:self.play_sound(n)).pack(side='left',padx=(0,8))
        actions=tk.Frame(outer,bg=bg);actions.pack(fill='x')
        self.advanced_button=ttk.Button(actions,text='高级设置  ▾',style='R.TButton',command=self.toggle_advanced)
        self.advanced_button.pack(side='left')
        ttk.Button(actions,text='打开日志',style='R.TButton',command=lambda:os.startfile(self.log.name)).pack(side='left',padx=8)
        ttk.Button(actions,text='停止监控',style='Stop.R.TButton',command=lambda:self.cancel('手动停止')).pack(side='right')
        self.advanced=tk.Frame(outer,bg=white,padx=14,pady=12)
        row=tk.Frame(self.advanced,bg=white);row.pack(fill='x')
        tk.Label(row,text='延时 / 毫秒',bg=white,fg=ink).pack(side='left')
        ttk.Entry(row,textvariable=self.delay_text,width=6).pack(side='left',padx=(8,10))
        tk.Label(row,text='采样 / Hz',bg=white,fg=ink).pack(side='left')
        ttk.Combobox(row,textvariable=self.hz_text,values=('15','30','60'),state='readonly',width=4,style='R.TCombobox').pack(side='left',padx=8)
        ttk.Button(row,text='保存',style='R.TButton',command=self.apply_delay).pack(side='right')
        ttk.Checkbutton(self.advanced,text='演练时收集顶部截图 · 最多 2 组 / 32MB',style='R.TCheckbutton',variable=self.collect_var,
            command=lambda:setattr(self,'collect_enabled',self.collect_var.get())).pack(anchor='w',pady=(8,0))
        tk.Label(self.advanced,textvariable=self.diagnostic_status,bg=white,fg=muted,wraplength=520,justify='left',font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(5,0))
        self.advanced_open=False
        self.fit_window()

    def fit_window(self):
        self.root.update_idletasks()
        self.root.geometry(f"{max(640,self.root.winfo_reqwidth())}x{self.root.winfo_reqheight()}")

    def toggle_advanced(self):
        self.advanced_open=not self.advanced_open
        if self.advanced_open:
            self.advanced.pack(fill='x',pady=(12,0))
            self.advanced_button.configure(text='高级设置  ▴')
        else:
            self.advanced.pack_forget()
            self.advanced_button.configure(text='高级设置  ▾')
        self.fit_window()

    def play_sound(self,name):
        winsound.PlaySound(str(Path(__file__).parent/'assets'/name),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)

    def apply_delay(self):
        self.cancel('修改延时，需重新按 F10')
        try:
            value=int(self.delay_text.get())
            if not 50<=value<=2000:raise ValueError()
            hz=int(self.hz_text.get())
            if hz not in (15,30,60):raise ValueError()
            self.config_path.write_text(json.dumps({'delay_ms':value,'capture_hz':hz}),encoding='utf-8')
            self.gate.delay_ms=value
            self.capture_hz=hz
            self.ui.put(('status',f'已关闭 · 已保存延时 {value} 毫秒、检测频率 {hz} 次/秒'))
        except (ValueError,OSError):
            self.ui.put(('status','未开启：请输入 50–2000 的整数，或检查目录写入权限'))

    def event(self,kind,**details):
        self.log.write(json.dumps(dict(time=time.time(),event=kind,**details),ensure_ascii=False)+'\n')
        if kind=='diagnostic_saved':self.ui.put(('diagnostic_saved',details['path']))

    def cancel(self,reason):
        with self.lock:
            self.token+=1;self.active=False;self.arming=False;self.gate.cancel()
            self.event('disarmed',reason=reason)
            self.ui.put(('status',f'已关闭 · {reason}'))

    def toggle(self):
        with self.lock:
            self.event('hotkey_received',key=self.key_name,active=self.active,arming=self.arming)
            if self.active or self.arming:
                self.cancel(self.key_name+' 关闭');return
            if len(self.hotkeys)!=1:return
            info=target()
            if not info:
                self.event('arm_rejected',reason='前台进程/窗口检查未通过')
                self.ui.put(('status','未开启：请将游戏置于主屏全屏/无边框并保持前台'));return
            self.token+=1;token=self.token;self.arming=True
            self.pending.put((token,info,self.dry.get()))
            self.ui.put(('status','正在检查当前 HUD，请保持游戏前台…'))

    def worker(self):
        while not self.closed:
            try: token,info,dry=self.pending.get(timeout=.05)
            except queue.Empty:continue
            with self.lock:
                if token!=self.token or self.closed:continue
            hz=self.capture_hz
            interval=1/hz
            self.gate.max_observation_gap=max(.12,2.5/hz)
            diagnostic=getattr(self,'diagnostics',None) if dry and getattr(self,'collect_enabled',False) else None
            if diagnostic:diagnostic.reset()
            camera=None
            extraction_guard=ExtractionGuard()
            extraction_trace=deque(maxlen=80);last_trace_time=0.
            region=(0,0,info[2],round(info[3]*240/1080))
            shape=(region[3],info[2])
            cap_ms=[];analysis_ms=[];attempts=0;new_frames=0
            window_start=time.perf_counter();cpu_start=time.process_time()
            def performance():
                nonlocal window_start,cpu_start,attempts,new_frames,cap_ms,analysis_ms
                wall=time.perf_counter()-window_start
                def percentile(values,p):
                    return sorted(values)[int((len(values)-1)*p)] if values else None
                if attempts:
                    self.event('performance',duration_s=round(wall,3),capture_region=list(region),capture_hz=hz,
                        attempts=attempts,new_frames=new_frames,checks_per_second=round(new_frames/max(wall,.001),2),
                        process_cpu_one_core_percent=round((time.process_time()-cpu_start)/max(wall,.001)*100,2),
                        capture_ms_p50=percentile(cap_ms,.50),capture_ms_p95=percentile(cap_ms,.95),
                        analysis_ms_p50=percentile(analysis_ms,.50),analysis_ms_p95=percentile(analysis_ms,.95))
                cap_ms=[];analysis_ms=[];attempts=0;new_frames=0
                window_start=time.perf_counter();cpu_start=time.process_time()
            try:
                import dxcam
                camera=dxcam.create(output_color='BGRA')
                deadline=time.perf_counter()+.75
                frame=None
                while time.perf_counter()<deadline:
                    with self.lock:
                        if token!=self.token or self.closed:break
                    frame=camera.grab(region=region,copy=False)
                    if frame is not None:break
                    time.sleep(interval)
                with self.lock:
                    if token!=self.token or self.closed:continue
                if frame is None:
                    self.cancel('采集暂时没有画面，请保持游戏前台，稍后重新按 F10');continue
                scores,menu_score=self.detector.analyze(frame,full_size=(info[2],info[3]))
                with self.lock:
                    if token!=self.token or self.closed:continue
                    self.arming=False
                    if target()!=info or frame.shape[:2]!=shape or not self.gate.arm(scores,menu_present=menu_score>=.55,combat_score=self.detector.last_features["combat_score"]):
                        self.event('arm_rejected',reason='HUD/窗口检查失败',scores=scores,menu_score=menu_score,
                            frame_size=list(frame.shape[:2]),expected_size=list(shape),features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}))
                        self.cancel('HUD 不匹配/不可见或窗口位置不符，未开启');continue
                    self.active=True
                    self.event('armed',dry_run=dry,scores=scores,menu_score=menu_score,
                        delay_ms=self.gate.delay_ms,capture_hz=hz,capture_region=list(region),features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}))
                    self.ui.put(('status','监控中 · '+('只记录，不退出' if dry else '战斗 HUD 和菜单均消失并超过设定延时才退出')))
                last_new=time.perf_counter();last_ui=0;paused=False;next_tick=last_new
                def collect(phase,stamp):
                    if diagnostic and self.collect_enabled and hasattr(self.detector,'last_top'):
                        diagnostic.push(self.detector.last_top,dict(monotonic=stamp,wall_time=time.time(),phase=phase,
                            features=dict(self.detector.last_features),menu_features=dict(self.detector.last_menu_features),
                            delay_ms=self.gate.delay_ms,capture_hz=hz,full_size=[info[2],info[3]],
                            absence_ms_before_decision=0 if self.gate.missing_since is None else (stamp-self.gate.missing_since)*1000))
                while True:
                    with self.lock:
                        if not self.active or token!=self.token or self.closed:break
                    wait=next_tick-time.perf_counter()
                    if wait>0:time.sleep(wait)
                    next_tick=max(next_tick+interval,time.perf_counter())
                    if target()!=info:
                        self.gate.missing_since=None;self.gate.missing=0
                        if not paused:
                            extraction_guard.reset()
                            extraction_trace.clear()
                            if diagnostic:diagnostic.reset()
                            self.event('focus_paused')
                            self.ui.put(('status','监控中 · 已切出游戏，暂停判断；切回自动继续'))
                            paused=True
                        time.sleep(.05);last_new=time.perf_counter();continue
                    if paused:
                        self.event('focus_resumed')
                        self.ui.put(('status','监控中 · 已回到游戏，继续判断'))
                        paused=False
                    begin=time.perf_counter()
                    frame=camera.grab(region=region,copy=False)
                    now=time.perf_counter();attempts+=1;cap_ms.append((now-begin)*1000)
                    if frame is None:
                        if now-last_new>.75:self.cancel('超过 0.75 秒无新画面');break
                        if now-window_start>=5:performance()
                        continue
                    new_frames+=1;last_new=now
                    if U.GetForegroundWindow()!=info[0]:
                        extraction_guard.reset()
                        self.gate.missing_since=None;self.gate.missing=0
                        if diagnostic:diagnostic.reset()
                        continue
                    if frame.shape[:2]!=shape:
                        self.cancel('采集区域尺寸改变');break
                    begin=time.perf_counter()
                    scores,menu_score=self.detector.analyze(frame,full_size=(info[2],info[3]))
                    collect('monitoring',now)
                    analysis_ms.append((time.perf_counter()-begin)*1000)
                    if now-last_ui>.2:
                        self.ui.put(('scores',f"战局特征 {self.detector.last_features['combat_score']:.0%} · 人形轮廓 {scores[0]:.0%} · 快捷栏 {scores[1]:.0%} · 菜单 {menu_score:.0%}"))
                        last_ui=now
                    with self.lock:
                        if token!=self.token or not self.active or self.closed:break
                        extracted=extraction_guard.update(now,self.detector.last_features['combat_score'],menu_score>=.55,getattr(self.detector,'last_extraction',None))
                        if now-last_trace_time>=.10 or extracted:
                            extraction_trace.append(dict(monotonic=now,combat_score=self.detector.last_features['combat_score'],menu_score=menu_score,
                                cues=dict(getattr(self.detector,'last_extraction',{})),guard=extraction_guard.trace(now)))
                            last_trace_time=now
                        if extracted:
                            self.event('extraction_transition',evidence=extraction_guard.last_confirmation,
                                extraction_cues=getattr(self.detector,'last_extraction',{}),extraction_trace=list(extraction_trace))
                            self.cancel('识别到撤离结束过渡，停止监控（未发送 Alt+F4）')
                            break
                        previous_missing=self.gate.missing_since
                        fired=self.gate.update(scores,menu_present=menu_score>=.55,now=now,combat_score=self.detector.last_features["combat_score"])
                        if previous_missing is not None and self.gate.missing_since is None and not fired:
                            self.event('absence_reset',combat_score=self.detector.last_features['combat_score'],menu_score=menu_score)
                        if fired:
                            self.active=False;self.token+=1
                            exit_watch=None
                            if target()!=info:message='未发送：游戏已失去前台'
                            elif dry:message='只记录：已检测到 UI 消失，未发送按键'
                            else:
                                delivery={}
                                process_handle=K.OpenProcess(0x100000,False,info[1])
                                message=send_alt_f4(info,delivery)
                                self.event('input_delivery',pid=info[1],hwnd=info[0],result=message,**delivery)
                                if '已发送一次 Alt+F4' in message:
                                    exit_watch=(process_handle,info[1],self.token)
                                elif process_handle:K.CloseHandle(process_handle)
                            self.event('trigger',scores=scores,menu_score=menu_score,delay_ms=self.gate.delay_ms,dry_run=dry,result=message,features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}),extraction_trace=list(extraction_trace))
                            if diagnostic:diagnostic.mark('candidate_trigger',dict(features=dict(self.detector.last_features),menu_features=dict(self.detector.last_menu_features)))
                            self.ui.put(('status','已关闭 · '+message))
                            if exit_watch:threading.Thread(target=self.observe_exit,args=exit_watch,daemon=True).start()
                    if fired:
                        # Evidence after a dry-run candidate only. Never send any
                        # further input, and stop collecting on focus/cancel.
                        trigger_token=self.token;tail_until=time.perf_counter()+.4
                        while diagnostic and not self.closed and self.token==trigger_token and time.perf_counter()<tail_until:
                            time.sleep(interval)
                            if target()!=info:break
                            frame=camera.grab(region=region,copy=False)
                            if frame is None:continue
                            if target()!=info:break
                            self.detector.analyze(frame,full_size=(info[2],info[3]))
                            collect('after_candidate',time.perf_counter())
                        break
                    if now-window_start>=5:performance()
            except Exception as exc:
                self.cancel('采集/检测错误：'+str(exc))
            finally:
                if diagnostic:diagnostic.reset()
                performance()
                if camera:
                    try:camera.release()
                    except Exception:pass
                    self.event('capture_released')

    def observe_exit(self,handle,pid,token):
        began=time.perf_counter()
        try:
            result=K.WaitForSingleObject(handle,3000) if handle else 0xFFFFFFFF
            if result==258:
                self.event('exit_observation',pid=pid,state='pending',wait_result=258,elapsed_ms=round((time.perf_counter()-began)*1000,1))
                self.ui.put(('exit_check',(token,'退出确认中 · 3 秒后进程仍在运行，继续观察至 30 秒')))
                result=K.WaitForSingleObject(handle,27000)
            state='exited' if result==0 else ('still_running' if result==258 else 'unavailable')
            self.event('exit_observation',pid=pid,state=state,wait_result=int(result),elapsed_ms=round((time.perf_counter()-began)*1000,1))
            message={'exited':'已确认游戏进程退出 · 回档结果需自行确认',
                'still_running':'退出未确认 · 发送后 30 秒游戏进程仍在运行',
                'unavailable':'无法确认游戏退出 · 进程观察不可用'}[state]
            self.ui.put(('exit_check',(token,message)))
        finally:
            if handle:K.CloseHandle(handle)

    def tick(self):
        try:
            while True:
                kind,text=self.ui.get_nowait()
                if kind=='hotkey':
                    self.toggle();continue
                if kind=='exit_check':
                    token,message=text
                    if token==self.token and not self.active and not self.arming:
                        self.status.set(message);self.state_head.set('本轮停止 · '+('游戏已退出' if message.startswith('已确认') else '请检查退出结果'))
                        self.bottom_status.set(message)
                    continue
                if kind=='diagnostic_saved':
                    self.diagnostic_status.set('诊断已保存：'+text);continue
                (self.status if kind=='status' else self.scores).set(text)
                if kind=='status':
                    if text.startswith('监控中'):head='已暂停 · 等待回到游戏' if '暂停' in text else '守护中 · F10 可停止'
                    elif text.startswith('正在检查'):head='正在连接战局…'
                    elif '只记录：已检测到' in text:head='候选已记录 · 本轮停止'
                    elif '已发送一次 Alt+F4' in text:head='已发送退出 · 本轮停止'
                    elif '未开启' in text or '失败' in text or '错误' in text:head='未连接 · 请检查提示'
                    else:head='待命 · 新的一轮'
                    self.state_head.set(head)
                    if text.startswith('监控中'):bottom='已暂停 · 切回游戏后继续' if '暂停' in text else '已开启 · F10 可关闭'
                    elif text.startswith('正在检查'):bottom='正在检查界面'
                    elif '已发送一次 Alt+F4' in text:bottom='退出按键已发送 · 正在确认游戏退出'
                    elif '只记录：已检测到' in text:bottom='演练候选已记录 · 未发送按键'
                    elif '撤离结束过渡' in text:bottom='撤离过渡已识别 · 监控停止'
                    elif '未开启' in text or '失败' in text or '错误' in text:bottom='未开启 · 请查看上方提示'
                    else:bottom='已关闭 · 等待开启'
                    self.bottom_status.set(bottom)
                if kind=='status' and not text.startswith('正在检查'):
                    feedback=self.notice.show(text)
                    self.event('status_feedback',status=text,**feedback)
        except queue.Empty:pass
        if not self.closed:self.root.after(10,self.tick)

    def close(self):
        self.cancel('程序退出');self.closed=True
        self.hotkey_service.close()
        self.notice.close()
        self.root.destroy()

if __name__=='__main__':
    mutex=K.CreateMutexW(None,False,'Local\\TarkovHUDMonitor_SingleInstance')
    if K.GetLastError()==183:
        U.MessageBoxW(None,'工具已经运行，请勿重复启动。','HUD 监控',0)
        sys.exit(0)
    try:
        app=App()
        if '--smoke-test' in sys.argv:
            import dxcam
            from dxcam.processor.cv2_processor import Cv2Processor
            assert not app.active and not app.arming
            assert len(app.hotkeys)==1, 'F10 registration failed'
            assert app.notice.available, 'Windows notification registration failed'
            import wave
            for name in ('enabled.wav','disabled.wav'):
                with wave.open(str(Path(__file__).parent/'assets'/name)) as audio:
                    assert audio.getnframes()>0 and audio.getframerate()==44100
            app.root.after(800,app.close)
        app.root.mainloop()
    except Exception as exc:
        if '--smoke-test' not in sys.argv:
            U.MessageBoxW(None,str(exc),'HUD 监控启动失败',0x10)
        raise
    finally:
        if mutex:K.CloseHandle(mutex)










