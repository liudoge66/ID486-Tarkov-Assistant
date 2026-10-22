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
from tkinter import ttk, filedialog
from tkinterdnd2 import TkinterDnD, DND_FILES
from sound_library import SoundLibrary
from primary_ui import PrimaryUI
from tkinter import simpledialog
from hotkey_service import Hotkeys, PRESETS as HOTKEY_PRESETS, label as hotkey_label, validate as validate_hotkey
from tkinter import font as tkfont
import winsound
from collections import deque
from detector import Detector, Gate, ExtractionGuard, ArmConfirmation, start_hud_evidence
from diagnostics import Diagnostics
from hud_layout import capture_strip_height
from keyboard_diagnostics import KeyboardDiagnostics
APP_BUILD='2026.10.22-v0.22-local'
APP_VERSION='V0.22'

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
        actual_exit='已发送一次 Alt+F4' in text or '已投递窗口关闭请求' in text
        filename='return_by_death.wav' if actual_exit and self.return_sound_enabled else ('enabled.wav' if enabled else 'disabled.wav')
        library=getattr(self,'sound_library',None)
        sound=library.resolve('exit' if actual_exit and self.return_sound_enabled else ('enabled' if enabled else 'disabled')) if library else Path(__file__).parent/'assets'/filename
        audio_ok=False
        audio_allowed=not library or (not library.muted and library.enabled['exit' if actual_exit else ('enabled' if enabled else 'disabled')])
        if library and actual_exit:sound=library.resolve('exit')
        if '--smoke-test' not in sys.argv and audio_allowed and sound is not None:
            try:
                library.play('exit' if actual_exit else ('enabled' if enabled else 'disabled')) if library else winsound.PlaySound(str(sound),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
                audio_ok=True
            except RuntimeError:pass
        # Keep the tray icon and app status, but never request Windows balloons.
        return dict(enabled=enabled,audio_requested=audio_ok,popup_accepted=False,windows_notifications_enabled=False)

    def close(self):
        if self.available:self.shell.Shell_NotifyIconW(2,C.byref(self.data))
        if self.owned_icon:U.DestroyIcon(self.owned_icon)

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

def _send_alt_f4(expected=None, report=None, target_check=None,on_keydown=None):
    # One attempt only. Never kill a process or retry an uncertain send.
    if any(U.GetAsyncKeyState(k)&0x8000 for k in (0x10,0x11,0x12,0x5B,0x5C)):
        return '未发送：Shift/Ctrl/Alt/Win 正被按住'
    if expected is not None and (target_check or target)()!=expected:return '未发送：目标窗口已失去前台'
    def packet(flags):
        return (INPUT*2)(*[INPUT(type=1,u=INPUTUNION(ki=KEYBDINPUT(v,0,flags,0,0))) for v in ((0x12,0x73) if flags==0 else (0x73,0x12))])
    began=time.perf_counter();C.set_last_error(0)
    if report is not None:report.setdefault('stages',[]).append(dict(stage='keydown_begin',monotonic_ns=time.perf_counter_ns(),wall_ns=time.time_ns()))
    down=U.SendInput(2,packet(0),C.sizeof(INPUT));down_error=C.get_last_error()
    if report is not None:report['stages'].append(dict(stage='keydown_end',monotonic_ns=time.perf_counter_ns(),wall_ns=time.time_ns()))
    try:
        if on_keydown is not None:on_keydown()
        if down==2:time.sleep(.040)
    finally:
        # Always release this chord, including partial sends and focus changes.
        C.set_last_error(0)
        if report is not None:report['stages'].append(dict(stage='keyup_begin',monotonic_ns=time.perf_counter_ns(),wall_ns=time.time_ns()))
        up=U.SendInput(2,packet(2),C.sizeof(INPUT));up_error=C.get_last_error()
        if report is not None:report['stages'].append(dict(stage='keyup_end',monotonic_ns=time.perf_counter_ns(),wall_ns=time.time_ns()))
    if report is not None:report.update(down_count=down,up_count=up,down_error=down_error,up_error=up_error,
        hold_ms=40,elapsed_ms=round((time.perf_counter()-began)*1000,2),foreground_after=int(U.GetForegroundWindow() or 0))
    if down!=2 or up!=2:
        return f'输入未完整发送（按下 {down}/2，松开 {up}/2），已停止，不重试'
    return '已发送一次 Alt+F4；是否退出/回档需自行确认'

def input_snapshot(stage):
    hwnd=U.GetForegroundWindow();pid=W.DWORD();U.GetWindowThreadProcessId(hwnd,C.byref(pid))
    keys={'LAlt':0xA4,'RAlt':0xA5,'LCtrl':0xA2,'RCtrl':0xA3,'LShift':0xA0,'RShift':0xA1,'LWin':0x5B,'RWin':0x5C,'F4':0x73}
    return dict(stage=stage,wall_ns=time.time_ns(),monotonic_ns=time.perf_counter_ns(),hwnd=int(hwnd or 0),pid=int(pid.value),keys={name:bool(U.GetAsyncKeyState(vk)&0x8000) for name,vk in keys.items()})

def send_alt_f4(expected=None,report=None,target_check=None,on_keydown=None):
    report=report if report is not None else {}
    report['stages']=[input_snapshot('before_send')]
    try:
        result=_send_alt_f4(expected,report,target_check=target_check,on_keydown=on_keydown);report['outcome']=result;return result
    finally:report['stages'].append(input_snapshot('after_send'))

U.IsWindow.argtypes=[W.HWND];U.IsWindow.restype=W.BOOL
U.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];U.PostMessageW.restype=W.BOOL

def post_window_close(expected,report):
    report['before']=input_snapshot('before_window_close')
    report['hwnd']=expected[0];report['pid']=expected[1];report['queued']=False
    # Never broadcast. Validate that the original window still belongs to the original PID.
    try:
        if not expected[0] or not U.IsWindow(expected[0]):report['state']='window_gone';return False
        pid=W.DWORD();U.GetWindowThreadProcessId(expected[0],C.byref(pid))
        if pid.value!=expected[1]:report['state']='owner_mismatch';return False
        C.set_last_error(0);report['post_begin_ns']=time.perf_counter_ns()
        ok=bool(U.PostMessageW(expected[0],0x0010,0,0));report['error']=C.get_last_error()
        report['post_end_ns']=time.perf_counter_ns();report['queued']=ok
        report['state']='queued_not_confirmed_processed' if ok else 'post_failed'
        return ok
    except Exception as exc:report['state']='exception';report['exception']=repr(exc);return False
    finally:report['after']=input_snapshot('after_window_close')

def send_exit_dual(expected,report=None,target_check=None):
    report=report if report is not None else {}
    report.update(request_id=str(time.time_ns()),policy='alt_f4_plus_wm_close',alt_f4={},window_close={},attribution='unknown_when_both_accepted')
    began=time.perf_counter_ns();posted=False
    def close_once():
        nonlocal posted
        if posted:return
        posted=True;post_window_close(expected,report['window_close'])
    try:
        alt=send_alt_f4(expected,report['alt_f4'],target_check=target_check,on_keydown=close_once)
    except Exception as exc:
        alt='按键发送异常：'+repr(exc);report['alt_f4']['exception']=repr(exc)
    finally:
        # Even a modifier-key rejection must not silently suppress the independent request.
        close_once()
    alt_ok='已发送一次 Alt+F4' in alt;close_ok=report['window_close'].get('queued',False)
    report['any_request_accepted']=alt_ok or close_ok;report['elapsed_ms']=(time.perf_counter_ns()-began)/1e6
    report['alt_f4']['accepted_into_input_stream']=alt_ok
    return ('已发送一次 Alt+F4' if alt_ok else alt)+'；'+('已投递窗口关闭请求' if close_ok else '窗口关闭请求未投递：'+report['window_close'].get('state','unknown'))+'；退出确认中'

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
        self.version_label=APP_VERSION
        self.root=TkinterDnD.Tk();self.root.title('ID486模拟助手');self.root.geometry('640x660');self.root.resizable(False,False)
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
        self.keyboard_diagnostics=KeyboardDiagnostics(base/'keyboard_diagnostics')
        self.event('keyboard_diagnostics_ready',available=self.keyboard_diagnostics.available,error=self.keyboard_diagnostics.error,pre_seconds=15,post_seconds=15,scope='monitor armed + foreground game PID; post-trigger 15s',temporary=True)
        self.config_path=base/'settings.json'
        try:self._loaded_preferences=json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self._loaded_preferences={}
        if not isinstance(self._loaded_preferences,dict):self._loaded_preferences={}
        self.sounds=SoundLibrary(base,Path(__file__).parent/'assets')
        if 'sound_profile' in self._loaded_preferences:self.sounds.load_profile(self._loaded_preferences['sound_profile'])
        self.sound_import_busy=False
        self.hotkey_config=base/'hotkey_settings.json'
        try:combo=validate_hotkey(self._loaded_preferences['hotkey_combo'] if 'hotkey_combo' in self._loaded_preferences else json.loads(self.hotkey_config.read_text(encoding='utf-8'))['combo'])
        except (OSError,ValueError,TypeError,KeyError):combo=(0,0x79)
        self.shortcut_dialog=None;self.recording=False
        self.hotkey_service=Hotkeys(self.ui,combo)
        if self._loaded_preferences.get("hotkey_enabled") is False:self.hotkey_service.clear()
        self.hotkeys=self.hotkey_service.registered
        self.key_name=self.hotkey_service.key_name
        self.key_label=tk.StringVar(value=self.key_name)
        self.key_hint=tk.StringVar(value=f'回到游戏后按 {self.key_name} 开启，再按一次停止')
        self.event('startup',hotkey=self.key_name,registered=bool(self.hotkeys),build=APP_BUILD,exit_policy='alt_f4_plus_wm_close')
        self.status=tk.StringVar(value=f'已关闭 · 进入战局、HUD 可见后按 {self.key_name}')
        self.scores=tk.StringVar(value='等待开启')
        self.dry=tk.BooleanVar(value=self._loaded_preferences.get('dry_run',True) is not False)
        self.collect_enabled=False
        self.collect_var=tk.BooleanVar(value=False)
        self.diagnostic_status=tk.StringVar(value='截图默认关闭；手动开启后仅候选附近保存，最多2组 / 32MB')
        self.diagnostics=Diagnostics(base/'diagnostics',self.event)
        self.config_path=base/'settings.json'
        try:
            configured=int(json.loads(self.config_path.read_text(encoding='utf-8'))['delay_ms'])
            if not 50<=configured<=2000:raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):configured=300
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
        self.notice.sound_library=self.sounds
        if not self.hotkeys:
            self.status.set(f'快捷键 {self.key_name} 已被占用，请点击快捷键设置选择其他按键')
        threading.Thread(target=self.worker,daemon=True).start()
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(10,self.tick)
        self.root.after(100,self.save_ui_theme)

    def build_ui(self):
        self.primary_only=True
        try:preferences=json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):preferences={}
        self.ui_theme=preferences.get('ui_theme','dark')
        self.state_head=tk.StringVar(value='未启动');self.bottom_status=tk.StringVar(value='等待开启')
        self.return_sound=tk.BooleanVar(value=self.sounds.enabled['exit'])
        self.sound_on_var=tk.BooleanVar(value=self.sounds.enabled['enabled'])
        self.sound_off_var=tk.BooleanVar(value=self.sounds.enabled['disabled'])
        self.mute_var=tk.BooleanVar(value=self.sounds.muted)
        self.log_var=tk.BooleanVar(value=preferences.get('detailed_logs',False) is True)
        self.detailed_logs=self.log_var.get()
        self.collect_var.set(preferences.get('collect_screenshots',False) is True);self.collect_enabled=self.collect_var.get()
        self.prep_feedback=tk.StringVar(value='');self.advanced_feedback=tk.StringVar(value='')
        self.sound_vars={};self.sound_boxes={};self.audio_window=None
        self.sound_hint=tk.StringVar(value='WAV / MP3 / FLAC / OGG · 50MB / 60秒以内')
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('R.TButton',background='#34343C',foreground='#FFFFFF',padding=(12,8),font=('Microsoft YaHei UI',10))
        style.map('R.TButton',background=[('active','#4A4A54')])
        style.configure('R.TCombobox',padding=5)
        self.runtime_dry=self.dry.get()
        for variable in (self.return_sound,self.sound_on_var,self.sound_off_var,self.mute_var,self.delay_text,self.hz_text,self.collect_var,self.log_var,self.dry):
            variable.trace_add('write',lambda *args:self.apply_runtime_settings())
        self.apply_runtime_settings()
        self.main_ui=PrimaryUI(self,Path(__file__).parent/'assets')

    def fit_window(self):
        if hasattr(self,'main_ui'):self.main_ui.schedule()

    def open_log(self):
        os.startfile(self.log.name)

    def write_preferences(self,**changes):
        try:values=json.loads(self.config_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):values={}
        values.update(changes)
        temporary=self.config_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,self.config_path)

    def save_ui_theme(self):
        # Theme is local to this app, including the native title bar where supported.
        try:
            value=C.c_int(self.ui_theme=='dark');hwnd=U.GetAncestor(self.root.winfo_id(),2)
            C.WinDLL('dwmapi').DwmSetWindowAttribute(W.HWND(hwnd),20,C.byref(value),4)
        except (OSError,AttributeError):pass

    def apply_runtime_settings(self):
        try:
            delay=int(self.delay_text.get());hz=int(self.hz_text.get())
            if not 50<=delay<=2000 or hz not in (15,30,60):return False
        except (ValueError,TypeError):return False
        with self.lock:
            changed=(self.gate.delay_ms!=delay or self.capture_hz!=hz)
            self.gate.delay_ms=delay;self.capture_hz=hz
            if changed:self.gate.missing_since=None;self.gate.last_observation=None;self.gate.missing=0
            self.collect_enabled=self.collect_var.get();self.detailed_logs=self.log_var.get()
            self.sounds.enabled=dict(enabled=self.sound_on_var.get(),disabled=self.sound_off_var.get(),exit=self.return_sound.get())
            was_muted=self.sounds.muted;self.sounds.muted=self.mute_var.get()
            if self.sounds.muted and not was_muted:self.sounds.stop()
            if hasattr(self,'notice'):self.notice.return_sound_enabled=self.return_sound.get()
            mode=self.dry.get()
            if mode!=self.runtime_dry and (self.active or self.arming):self.cancel('模式已切换，请重新开启')
            self.runtime_dry=mode
        return True

    def persist_session(self):
        if not self.apply_runtime_settings():raise ValueError('invalid settings')
        self.write_preferences(profile_version=1,delay_ms=self.gate.delay_ms,capture_hz=self.capture_hz,
            collect_screenshots=self.collect_enabled,detailed_logs=self.detailed_logs,ui_theme=self.ui_theme,
            dry_run=self.dry.get(),hotkey_combo=list(self.hotkey_service.combo),hotkey_enabled=bool(self.hotkeys),
            hotkey_choices=[list(c) for c in self.main_ui.key_choices],sound_profile=self.sounds.profile())

    def save_preparation(self):
        try:self.persist_session();self.prep_feedback.set('音效与快捷键已保存')
        except (OSError,ValueError):self.prep_feedback.set('保存失败，请检查设置或目录写入权限')

    def save_advanced(self):
        try:self.persist_session();self.advanced_feedback.set('进阶自定义设置已保存')
        except (OSError,ValueError):self.advanced_feedback.set('保存失败：延时需为50–2000毫秒，采样为15/30/60 Hz')

    def edit_delay(self):
        if getattr(self,'primary_only',False):return
        value=simpledialog.askinteger('延时设置','持续缺失多少毫秒后触发（50–2000）',initialvalue=int(self.delay_text.get()),minvalue=50,maxvalue=2000,parent=self.root)
        if value is not None:self.delay_text.set(str(value));self.advanced_feedback.set('尚未保存')

    def edit_hz(self):
        if getattr(self,'primary_only',False):return
        menu=tk.Menu(self.root,tearoff=False)
        for hz in (15,30,60):menu.add_command(label=f'{hz} Hz',command=lambda h=hz:(self.hz_text.set(str(h)),self.advanced_feedback.set('尚未保存')))
        menu.tk_popup(self.root.winfo_pointerx(),self.root.winfo_pointery())

    def open_sound_settings(self):
        if getattr(self,'primary_only',False):return
        if self.audio_window is not None:self.audio_window.lift();return
        win=tk.Toplevel(self.root);self.audio_window=win;win.title('音效设置 · 二级界面待定')
        win.configure(bg='#18181C');win.transient(self.root);win.resizable(False,False)
        tk.Label(win,text='当前保留现有功能入口，二级布局待确认',bg='#18181C',fg='#999999',font=('Microsoft YaHei UI',10)).pack(anchor='w',padx=16,pady=(14,8))
        self.sound_vars={};self.sound_boxes={}
        for key,title in (('enabled','启动'),('disabled','关闭'),('exit','大退')):
            row=tk.Frame(win,bg='#18181C');row.pack(fill='x',padx=16,pady=6)
            tk.Label(row,text=title,bg='#18181C',fg='white',width=4).pack(side='left')
            var=tk.StringVar(value=self.sounds.selected[key]);self.sound_vars[key]=var
            box=ttk.Combobox(row,textvariable=var,values=self.sounds.choices(),state='readonly',width=30);box.pack(side='left',padx=8);self.sound_boxes[key]=box
            box.bind('<<ComboboxSelected>>',lambda e,k=key:self.select_sound(k))
            ttk.Button(row,text='试听',style='R.TButton',command=lambda k=key:self.play_sound(k)).pack(side='left',padx=4)
            ttk.Button(row,text='导入',style='R.TButton',command=lambda k=key:self.pick_sound(k)).pack(side='left')
            for widget in (row,box):
                widget.drop_target_register(DND_FILES);widget.dnd_bind('<<Drop>>',lambda e,k=key:self.drop_sound(k,e.data))
        tk.Label(win,textvariable=self.sound_hint,bg='#18181C',fg='#999999',wraplength=560).pack(padx=16,pady=8)
        ttk.Button(win,text='停止试听',style='R.TButton',command=self.sounds.stop).pack(anchor='e',padx=16,pady=(0,12))
        def close():self.sound_vars={};self.sound_boxes={};self.audio_window=None;win.destroy()
        win.protocol('WM_DELETE_WINDOW',close)

    def play_sound(self,name):
        if self.sounds.muted:
            self.sound_hint.set('静音模式已开启，请关闭静音并保存后再试听');return
        key={'enabled.wav':'enabled','disabled.wav':'disabled','return_by_death.wav':'exit'}.get(name,name)
        sound=self.sounds.resolve(key)
        if sound is None:return
        try:self.sounds.play(key)
        except (RuntimeError,OSError):self.sound_hint.set('播放失败，请重新选择或导入音效')

    def select_sound(self,key):
        try:
            self.sounds.choose(key,self.sound_vars[key].get());self.sound_hint.set('音效已即时生效 · 保存后下次保留');self.main_ui.schedule()
        except OSError:
            self.sound_vars[key].set(self.sounds.selected[key]);self.sound_hint.set('保存失败，请检查程序目录写入权限')

    def open_shortcuts(self):
        if getattr(self,'primary_only',False):return
        if self.shortcut_dialog is not None:self.shortcut_dialog.lift();return
        self.cancel('快捷键设置中，保存后需重新开启')
        win=tk.Toplevel(self.root);self.shortcut_dialog=win;win.title('监控开启 / 关闭快捷键')
        self.shortcut_refresh=None
        win.configure(bg='#F4F1FC');win.resizable(False,False);win.transient(self.root)
        self.hotkey_pick=tk.StringVar(value=','.join(map(str,self.hotkey_service.combo)))
        self.hotkey_note=tk.StringVar(value='灰色删除线：当前无法注册；保存前再次检查')
        self.hotkey_rows=[];self.custom_combo=None
        normal=tkfont.Font(family='Microsoft YaHei UI',size=11)
        strike=tkfont.Font(family='Microsoft YaHei UI',size=11,overstrike=True)
        self.shortcut_fonts=(normal,strike)
        tk.Label(win,text='预设快捷键',bg='#F4F1FC',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w',padx=18,pady=(15,4))
        choices=list(HOTKEY_PRESETS)
        if self.hotkey_service.combo not in choices:choices.append(self.hotkey_service.combo)
        for combo in choices:
            button=tk.Radiobutton(win,text=hotkey_label(combo),variable=self.hotkey_pick,value=','.join(map(str,combo)),bg='#F4F1FC',anchor='w',font=normal)
            button.pack(fill='x',padx=18);self.hotkey_rows.append((combo,button))
        ttk.Button(win,text='录制自定义快捷键',style='R.TButton',command=lambda:self.begin_record()).pack(fill='x',padx=18,pady=10)
        self.custom_radio=tk.Radiobutton(win,text='尚未录制',variable=self.hotkey_pick,value='custom',state='disabled',bg='#F4F1FC',anchor='w',font=normal)
        self.custom_radio.pack(fill='x',padx=18)
        tk.Label(win,textvariable=self.hotkey_note,bg='#F4F1FC',wraplength=390,justify='left').pack(fill='x',padx=18,pady=8)
        tk.Label(win,text='仅检测 Windows 系统热键占用，无法检测游戏内按键绑定。\n单键支持功能键/导航键；字母数字请搭配 Ctrl、Alt 或 Shift。\n录制时按 Esc 取消；F12 保留用于截图。',bg='#F4F1FC',fg='#74678B',justify='left',wraplength=390).pack(padx=18,pady=(0,12))
        self.hotkey_apply=ttk.Button(win,text='保存快捷键',style='R.TButton',command=self.apply_shortcut)
        self.hotkey_apply.pack(fill='x',padx=18,pady=(0,15))
        def close():
            if self.shortcut_refresh is not None:win.after_cancel(self.shortcut_refresh)
            self.recording=False;self.shortcut_dialog=None;win.destroy()
        win.protocol('WM_DELETE_WINDOW',close)
        win.bind('<KeyPress>',self.record_key)
        self.refresh_shortcuts()

    def begin_record(self):
        self.recording=True;self.hotkey_apply.configure(state='disabled')
        self.hotkey_note.set('请按下一个按键或组合键（Esc 取消）');self.shortcut_dialog.focus_set()

    def record_key(self,event):
        if not self.recording:return
        if event.keysym=='Escape':self.recording=False;self.hotkey_note.set('已取消录制');return 'break'
        vk=event.keycode
        if vk in (16,17,18,91,92,160,161,162,163,164,165):return 'break'
        if any(U.GetAsyncKeyState(k)&0x8000 for k in (91,92)):
            self.hotkey_note.set('不支持 Windows 键组合，请重新录制');return 'break'
        mods=sum(bit for key,bit in ((17,2),(18,1),(16,4)) if U.GetAsyncKeyState(key)&0x8000)
        self.record_combo((mods,vk));return 'break'

    def record_combo(self,combo):
        try:combo=validate_hotkey(combo)
        except ValueError as exc:self.hotkey_note.set(str(exc));return
        self.recording=False;self.custom_combo=combo;self.hotkey_pick.set('custom')
        self.custom_radio.configure(text=hotkey_label(combo));self.hotkey_note.set('已录制 '+hotkey_label(combo)+'，保存后生效')
        self.check_shortcuts()

    def check_shortcuts(self):
        normal,strike=self.shortcut_fonts
        for combo,button in self.hotkey_rows:
            free=self.hotkey_service.available(combo)
            button.configure(state='normal' if free else 'disabled',fg='#322A44' if free else '#999999',font=normal if free else strike)
        if self.custom_combo is not None:
            free=self.hotkey_service.available(self.custom_combo)
            self.custom_radio.configure(state='normal' if free else 'disabled',fg='#322A44' if free else '#999999',font=normal if free else strike)
        choice=self.hotkey_pick.get()
        combo=self.custom_combo if choice=='custom' else tuple(map(int,choice.split(',')))
        free=combo is not None and self.hotkey_service.available(combo)
        self.hotkey_apply.configure(state='normal' if free and not self.recording else 'disabled')

    def refresh_shortcuts(self):
        if self.shortcut_dialog is None:return
        try:self.check_shortcuts()
        except RuntimeError:self.hotkey_note.set('系统热键检查超时，请关闭设置后重试')
        self.shortcut_refresh=self.shortcut_dialog.after(1500,self.refresh_shortcuts)

    def apply_shortcut(self):
        choice=self.hotkey_pick.get();combo=self.custom_combo if choice=='custom' else tuple(map(int,choice.split(',')))
        if combo is None:return
        try:
            if not self.hotkey_service.rebind(combo):
                self.hotkey_note.set('该快捷键已被占用，未修改原快捷键');self.check_shortcuts();return
            self.key_name=self.hotkey_service.key_name;self.hotkeys=self.hotkey_service.registered
            self.key_label.set(self.key_name);self.key_hint.set(f'回到游戏后按 {self.key_name} 开启，再按一次停止')
            self.notice.data.szTip=f'ID486模拟助手 · {self.key_name} 开启/关闭'
            if getattr(self.notice,'available',False):
                self.notice.data.uFlags=4;self.notice.shell.Shell_NotifyIconW(1,C.byref(self.notice.data))
            self.event('hotkey_changed',key=self.key_name)
            if self.shortcut_refresh is not None:self.shortcut_dialog.after_cancel(self.shortcut_refresh)
            self.recording=False;self.shortcut_dialog.destroy();self.shortcut_dialog=None
            self.ui.put(('status',f'已关闭 · 快捷键已设为 {self.key_name}，请回到游戏后开启'))
        except (ValueError,RuntimeError) as exc:self.hotkey_note.set(str(exc))

    def pick_sound(self,key):
        path=filedialog.askopenfilename(parent=self.root,title='选择音效（最多60秒 / 50MB）',filetypes=[('音频文件','*.wav *.mp3 *.flac *.ogg'),('所有文件','*.*')])
        if path:self.import_sound(key,path)

    def drop_sound(self,key,data):
        paths=self.root.tk.splitlist(data)
        if len(paths)!=1:self.sound_hint.set('每次请拖入一个音频文件');return
        self.import_sound(key,paths[0])

    def import_sound(self,key,path):
        if self.sound_import_busy:self.sound_hint.set('正在导入，请稍候');return
        if self.active or self.arming:self.sound_hint.set('请先按快捷键停止监控，再导入音频');return
        self.sound_import_busy=True;self.sound_hint.set('正在转换音频，完成后自动保存…')
        def convert():
            try:result=self.sounds.prepare_import(path);self.ui.put(('sound_imported',(key,result,None)))
            except Exception as exc:self.ui.put(('sound_imported',(key,None,str(exc))))
        threading.Thread(target=convert,daemon=True).start()

    def apply_delay(self):
        self.advanced_feedback.set('已即时生效，保存后下次保留' if self.apply_runtime_settings() else '延时或采样值无效')

    def event(self,kind,**details):
        if not getattr(self,'detailed_logs',False):
            if kind in ('performance','absence_reset'):return
            details.pop('extraction_trace',None)
        self.log.write(json.dumps(dict(time=time.time(),event=kind,**details),ensure_ascii=False)+'\n')
        if kind=='diagnostic_saved':self.ui.put(('diagnostic_saved',details['path']))

    def cancel(self,reason):
        with self.lock:
            self.token+=1;self.active=False;self.arming=False;self.gate.cancel()
            if getattr(self,'keyboard_diagnostics',None):self.keyboard_diagnostics.pause()
            self.event('disarmed',reason=reason)
            self.ui.put(('status',f'已关闭 · {reason}'))

    def video_feedback(self,result):
        outcome=result.get('outcome','test_error');video=result.get('video','录像')
        if outcome in ('player_closed_after_dual_request','player_closed_after_alt_f4') and result.get('player_exit_code',0)==0:
            headline='录像已退出';caption='死亡候选触发 · 已确认测试播放器关闭';sound_key='exit'
        elif outcome=='extraction_protected':
            headline='录像已结束';caption='已识别成功撤离 · 未发送退出请求';sound_key='disabled'
        elif outcome=='no_trigger_before_end':
            headline='录像已结束';caption='播放完毕 · 未触发死亡或撤离候选';sound_key=None
        elif outcome=='closed_by_user':
            headline='录像已关闭';caption='用户手动关闭 · 未触发大退';sound_key='disabled'
        else:
            headline='录像退出未确认';caption='测试未完成或退出失败 · 请查看录像测试记录';sound_key=None
        text=headline+' · '+caption
        self.status.set(caption);self.bottom_status.set(text);self.state_head.set(headline)
        played=False;error=None
        if sound_key and not self.sounds.muted and self.sounds.enabled.get(sound_key,False):
            path=self.sounds.resolve(sound_key)
            if path is not None:
                try:
                    played=self.sounds.play(sound_key)
                except (RuntimeError,OSError) as exc:error=str(exc)
        self.event('video_test_feedback',video=video,outcome=outcome,status=text,sound_key=sound_key,
            sound_choice=self.sounds.selected.get(sound_key) if sound_key else None,played=played,error=error)

    def open_video_test(self):
        if getattr(self,'video_testing',False):self.prep_feedback.set('录像测试进行中，请等待当前播放结束');return
        if self.active or self.arming:self.cancel('进入录像测试前停止监控')
        else:
            with self.lock:self.token+=1  # Invalidate stale game-exit UI callbacks.
        paths=filedialog.askopenfilenames(parent=self.root,title='选择录像测试，可多选',filetypes=[('录像','*.mp4 *.mkv *.avi *.mov')])
        if not paths:return
        import subprocess
        folder=Path(self.log.name).parent/'video_test_results';folder.mkdir(exist_ok=True)
        self.video_testing=True
        def replay():
            try:
                for path in paths:
                    output=folder/('replay_'+str(time.time_ns())+'.json')
                    self.ui.put(('video_test_started',Path(path).name))
                    args=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).resolve())]
                    try:
                        done=subprocess.run(args+['--video-test',path,'--video-result',str(output)],check=False)
                        result=json.loads(output.read_text(encoding='utf-8')) if output.exists() else dict(outcome='test_error',video=Path(path).name)
                        result['player_exit_code']=done.returncode
                    except (OSError,ValueError) as exc:result=dict(outcome='test_error',video=Path(path).name,error=str(exc))
                    self.ui.put(('video_test_result',result))
            finally:self.ui.put(('video_test_batch_done',None))
        threading.Thread(target=replay,daemon=True).start()

    def toggle(self):
        with self.lock:
            if getattr(self,'video_testing',False):
                self.prep_feedback.set('录像测试进行中，请先关闭测试播放器');return
            if self.sound_import_busy:
                self.sound_hint.set('音频转换完成后即可开启监控');return
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
            diagnostic=getattr(self,'diagnostics',None) if getattr(self,'collect_enabled',False) else None
            if diagnostic:diagnostic.reset()
            camera=None
            extraction_guard=ExtractionGuard()
            extraction_trace=deque(maxlen=80);last_trace_time=0.
            region=(0,0,info[2],capture_strip_height(info[2],info[3]))
            if hasattr(self.detector,"reset_layout"):self.detector.reset_layout()
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
                deadline=time.perf_counter()+.5
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
                    self.cancel('采集暂时没有画面，请保持游戏前台，稍后重新按快捷键');continue
                confirmation=ArmConfirmation(hz);confirmed=False;arm_deadline=deadline
                scores=(0.,0.);menu_score=0.
                while time.perf_counter()<arm_deadline:
                    with self.lock:
                        if token!=self.token or self.closed:break
                    if target()!=info:break
                    if frame is not None:
                        if frame.shape[:2]!=shape:break
                        scores,menu_score=self.detector.analyze(frame,full_size=(info[2],info[3]))
                        if menu_score<.55 and confirmation.update(self.detector.last_features,time.perf_counter()):confirmed=True;break
                    time.sleep(interval);frame=camera.grab(region=region,copy=False)
                with self.lock:
                    if token!=self.token or self.closed:continue
                    self.arming=False
                    if not confirmed or target()!=info or not self.gate.arm(scores,menu_present=menu_score>=.55,combat_score=self.detector.last_features["combat_score"],features=self.detector.last_features):
                        self.event('arm_rejected',reason='首次开启需要战斗HUD，菜单不能单独开启',arm_policy=confirmation.summary(),scores=scores,menu_score=menu_score,
                            frame_size=list(frame.shape[:2]) if frame is not None else None,expected_size=list(shape),features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}))
                        self.cancel('未观察到战斗HUD，请在血量人形与快捷栏可见时开启');continue
                    self.active=True
                    if getattr(self,'keyboard_diagnostics',None):self.keyboard_diagnostics.start(info[1])
                    self.event('armed',arm_policy=confirmation.summary(),dry_run=dry,scores=scores,menu_score=menu_score,
                        delay_ms=self.gate.delay_ms,capture_hz=hz,capture_region=list(region),features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}))
                    self.ui.put(('status','监控中 · '+('只记录，不退出' if dry else '战斗 HUD 和菜单均消失并超过设定延时才退出')))
                last_new=time.perf_counter();last_ui=0;paused=False;capture_waiting=False;settings_transition=False;next_tick=last_new
                def collect(phase,stamp):
                    if diagnostic and self.collect_enabled and hasattr(self.detector,'last_top'):
                        diagnostic.push(self.detector.last_top,dict(monotonic=stamp,wall_time=time.time(),phase=phase,
                            features=dict(self.detector.last_features),menu_features=dict(self.detector.last_menu_features),
                            delay_ms=self.gate.delay_ms,capture_hz=hz,full_size=[info[2],info[3]],
                            absence_ms_before_decision=0 if self.gate.missing_since is None else (stamp-self.gate.missing_since)*1000))
                while True:
                    with self.lock:
                        if not self.active or token!=self.token or self.closed:break
                        if hz!=self.capture_hz:
                            hz=self.capture_hz;interval=1/hz;next_tick=time.perf_counter()
                            self.gate.max_observation_gap=max(.12,2.5/hz)
                        wanted=self.diagnostics if self.collect_enabled else None
                        if wanted is not diagnostic:
                            if diagnostic:diagnostic.reset()
                            diagnostic=wanted
                            if diagnostic:diagnostic.reset()
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
                        if now-last_new>.75 and not capture_waiting:
                            capture_waiting=True
                            self.event('capture_waiting',no_new_frame_s=round(now-last_new,3))
                            self.ui.put(('status','监控中 · 暂无新画面，等待采集恢复'))
                        if now-window_start>=5:performance()
                        continue
                    if capture_waiting:
                        self.event('capture_resumed',no_new_frame_s=round(now-last_new,3))
                        self.ui.put(('status','监控中 · 采集已恢复，继续判断'))
                        capture_waiting=False
                    new_frames+=1;last_new=now
                    if U.GetForegroundWindow()!=info[0]:
                        extraction_guard.reset()
                        self.gate.missing_since=None;self.gate.missing=0
                        if diagnostic:diagnostic.reset()
                        continue
                    if frame.shape[:2]!=shape:
                        self.cancel('采集区域尺寸改变');break
                    begin=time.perf_counter()
                    # Settings can change the viewport without changing capture dimensions.
                    # Reacquire layout while guarded; a stale viewport must not imply death.
                    if settings_transition and hasattr(self.detector,'reset_layout'):self.detector.reset_layout()
                    scores,menu_score=self.detector.analyze(frame,full_size=(info[2],info[3]))
                    settings_visible=self.detector.last_menu_features.get('settings',0.)>=.55
                    if settings_visible and not settings_transition:
                        settings_transition=True
                        extraction_guard.reset('settings_transition')
                        self.event('settings_transition_paused',layout=self.detector.last_features.get('layout'))
                        self.ui.put(('status','监控中 · 设置页面已打开，等待返回战斗画面'))
                    if settings_transition:
                        if start_hud_evidence(self.detector.last_features) and menu_score<.55:
                            settings_transition=False
                            extraction_guard.reset('settings_transition_resumed')
                            self.gate.missing_since=None;self.gate.missing=0
                            self.event('settings_transition_resumed',layout=self.detector.last_features.get('layout'))
                            self.ui.put(('status','监控中 · 已重新定位战斗HUD，继续判断'))
                        else:
                            menu_score=max(menu_score,1.)
                            self.detector.last_menu_features['settings_transition_guard']=True
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
                            self.cancel('识别到撤离渐黑过渡，停止监控（未发送 Alt+F4）')
                            break
                        previous_missing=self.gate.missing_since
                        fired=self.gate.update(scores,menu_present=menu_score>=.55,now=now,combat_score=self.detector.last_features["combat_score"])
                        if previous_missing is not None and self.gate.missing_since is None and not fired:
                            self.event('absence_reset',combat_score=self.detector.last_features['combat_score'],menu_score=menu_score)
                        if fired:
                            if getattr(self,'keyboard_diagnostics',None):
                                self.keyboard_diagnostics.trigger(info[1],dict(candidate=input_snapshot('candidate'),dry_run=dry,combat_score=self.detector.last_features['combat_score'],delay_ms=self.gate.delay_ms))
                            self.active=False;self.token+=1
                            exit_watch=None
                            if target()!=info:message='未发送：游戏已失去前台'
                            elif dry:message='只记录：已检测到 UI 消失，未发送按键'
                            else:
                                delivery={}
                                process_handle=K.OpenProcess(0x100000,False,info[1])
                                message=send_exit_dual(info,delivery)
                                self.event('input_delivery',pid=info[1],hwnd=info[0],result=message,**delivery)
                                self.event('alt_f4_delivery',request_id=delivery['request_id'],pid=info[1],hwnd=info[0],**delivery['alt_f4'])
                                self.event('window_close_delivery',request_id=delivery['request_id'],**delivery['window_close'])
                                if delivery.get('any_request_accepted',False):
                                    exit_watch=(process_handle,info[1],self.token)
                                elif process_handle:K.CloseHandle(process_handle)
                            self.event('trigger',scores=scores,menu_score=menu_score,delay_ms=self.gate.delay_ms,dry_run=dry,result=message,features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}),extraction_trace=list(extraction_trace))
                            if diagnostic:diagnostic.mark('candidate_trigger',dict(features=dict(self.detector.last_features),menu_features=dict(self.detector.last_menu_features)))
                            self.ui.put(('status','已关闭 · '+message))
                            if exit_watch:threading.Thread(target=self.observe_exit,args=exit_watch,daemon=True).start()
                    if fired:
                        # Optional bounded evidence after a candidate. Never send any
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
                if kind=='video_test_started':
                    message='录像测试中 · '+text
                    self.status.set('正在播放 · '+text);self.bottom_status.set(message);self.state_head.set('录像测试中');continue
                if kind=='video_test_result':self.video_feedback(text);continue
                if kind=='video_test_batch_done':self.video_testing=False;continue
                if kind=='hotkey':
                    if self.main_ui.key_recording:
                        self.main_ui.select_key(self.hotkey_service.combo);continue
                    
                    if self.shortcut_dialog is not None:
                        if self.recording:self.record_combo(self.hotkey_service.combo)
                    else:self.toggle()
                    continue
                if kind=='sound_imported':
                    key,result,error=text;self.sound_import_busy=False
                    if not error:
                        try:
                            self.sounds.register(key,*result)
                            for box in self.sound_boxes.values():box.configure(values=self.sounds.choices())
                            
                            if key in self.sound_vars:self.sound_vars[key].set(result[0])
                            self.sound_hint.set('已导入副本并即时生效 · 保存后下次保留')
                        except OSError:error='音效设置保存失败，请检查目录写入权限'
                    if error:self.sound_hint.set('导入失败：'+error)
                    self.main_ui.schedule();self.fit_window();continue
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
                    if text.startswith('监控中'):head='已暂停 · 等待回到游戏' if '暂停' in text else f'守护中 · {self.key_name} 可停止'
                    elif text.startswith('正在检查'):head='正在连接战局…'
                    elif '只记录：已检测到' in text:head='候选已记录 · 本轮停止'
                    elif '已发送一次 Alt+F4' in text:head='已发送退出 · 本轮停止'
                    elif '未开启' in text or '失败' in text or '错误' in text:head='未连接 · 请检查提示'
                    else:head='待命 · 新的一轮'
                    self.state_head.set(head)
                    if text.startswith('监控中'):bottom='已暂停 · 切回游戏后继续' if '暂停' in text else f'已开启 · {self.key_name} 可关闭'
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
        self.sounds.stop()
        self.hotkey_service.close()
        if getattr(self,'keyboard_diagnostics',None):self.keyboard_diagnostics.close()
        self.notice.close()
        self.root.destroy()

if __name__=='__main__':
    if '--video-test' in sys.argv:
        from video_test import run_video_test
        pos=sys.argv.index('--video-test')
        result_path=sys.argv[sys.argv.index('--video-result')+1] if '--video-result' in sys.argv else None
        sys.exit(run_video_test(sys.argv[pos+1] if len(sys.argv)>pos+1 else None,result_path=result_path))
    mutex=K.CreateMutexW(None,False,'Local\\TarkovHUDMonitor_SingleInstance')
    if K.GetLastError()==183:
        U.MessageBoxW(None,'工具已经运行，请勿重复启动。','HUD 监控',0)
        sys.exit(0)
    try:
        app=App()
        if '--smoke-test' in sys.argv:
            import video_test
            import dxcam
            from dxcam.processor.cv2_processor import Cv2Processor
            assert not app.active and not app.arming
            assert len(app.hotkeys)==(0 if app._loaded_preferences.get('hotkey_enabled') is False else 1), 'Configured hotkey registration failed'
            assert app.notice.available, 'Windows notification registration failed'
            import wave
            for name in ('enabled.wav','disabled.wav'):
                with wave.open(str(Path(__file__).parent/'assets'/name)) as audio:
                    assert audio.getnframes()>0 and audio.getframerate()==44100
            import tempfile
            with tempfile.TemporaryDirectory() as test_audio:
                library=SoundLibrary(test_audio,Path(__file__).parent/'assets')
                choice,filename=library.prepare_import(Path(__file__).parent/'assets'/'return_by_death.mp3')
                library.register('exit',choice,filename)
                assert library.resolve('exit').is_file()
            assert app.hotkey_service.available(app.hotkey_service.combo)
            app.root.after(800,app.close)
        app.root.mainloop()
    except Exception as exc:
        if '--smoke-test' in sys.argv:
            if 'app' in locals():
                app.event('smoke_error',error=repr(exc));app.close()
            sys.exit(1)
        if '--smoke-test' not in sys.argv:
            U.MessageBoxW(None,str(exc),'HUD 监控启动失败',0x10)
        raise
    finally:
        if mutex:K.CloseHandle(mutex)










