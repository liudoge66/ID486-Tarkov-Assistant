import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
import sys
import threading
import numpy as np
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog
from tkinterdnd2 import TkinterDnD, DND_FILES
from sound_library import SoundLibrary
from app_storage import load_settings, writable_data_directory
from primary_ui import PrimaryUI
from tkinter import simpledialog
from hotkey_service import Hotkeys, PRESETS as HOTKEY_PRESETS, label as hotkey_label, validate as validate_hotkey
from tkinter import font as tkfont
import winsound
from collections import deque
from detector import Detector, Gate, ExtractionGuard, ArmConfirmation, start_hud_evidence, combat_hud_evidence
from diagnostics import Diagnostics
from hud_layout import capture_strip_height
from esc_title import EscTitleDetector
from esc_audit import EscAudit,title_skip_reason
from ui_fade import UniformFadeWatch
from transfer import TransferDetector,TransferFlow,resume_hud
from keyboard_diagnostics import KeyboardDiagnostics
from decision import DecisionEngine
from process_exit import GameProcess
from edition import FENCE, NETWORK, EDITION, VERSION, BUILD
from event_log import EventJournal
from isolation_candidate import IsolationCandidate, reviewed_candidate
from settlement_watch import SettlementWatch
from shared_capture import SharedCapture
from review_policy import full_frame_can_continue,scope_expansion_can_preserve
from parallel_review import HudContinuity,TitleTicket,ReviewTicket,menu_kind
from auto_capture import AutoCapture
from settlement_fast import FastProbe,ParallelSettlementCheck
from interaction_ui import SteamOverlayState,confirmed_game_ui
from window_icons import WindowIcons, icon_path, load_icon, window_dpi, metric, set_app_identity
APP_BUILD=BUILD
APP_VERSION=VERSION

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
        self.owned_icon=load_icon(metric(49,window_dpi(self.data.hWnd)),metric(50,window_dpi(self.data.hWnd)))
        if self.owned_icon:self.data.hIcon=self.owned_icon
        self.available=bool(self.shell.Shell_NotifyIconW(0,C.byref(self.data)))

    def show(self, text):
        # Audio is independent of Windows notification suppression. Async playback
        # never delays the capture worker or takes focus from the game.
        enabled=text.startswith('监控中') and '暂停' not in text
        actual_exit='已发送一次 Alt+F4' in text or '已投递窗口关闭请求' in text or '已请求结束游戏进程' in text
        transition=text.startswith('监控中') and any(word in text for word in ('暂停','恢复','继续判断'))
        audio_key='exit' if actual_exit else ('enabled' if enabled or transition else 'disabled')
        filename='return_by_death.wav' if audio_key=='exit' else audio_key+'.wav'
        library=getattr(self,'sound_library',None)
        sound=library.resolve(audio_key) if library else Path(__file__).parent/'assets'/filename
        audio_ok=False
        audio_allowed=not library or (not library.muted and library.enabled[audio_key])
        if '--smoke-test' not in sys.argv and audio_allowed and sound is not None:
            try:
                library.play(audio_key) if library else winsound.PlaySound(str(sound),winsound.SND_FILENAME|winsound.SND_ASYNC|winsound.SND_NODEFAULT)
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

def foreground_owner(expected):
    if U.GetForegroundWindow()!=expected[0]:return False
    pid=W.DWORD();U.GetWindowThreadProcessId(expected[0],C.byref(pid))
    return pid.value==expected[1]

def geometry_signature(expected):
    rect=W.RECT();origin=W.POINT(0,0)
    if not U.GetClientRect(expected[0],C.byref(rect)) or not U.ClientToScreen(expected[0],C.byref(origin)):return None
    return (U.GetSystemMetrics(0),U.GetSystemMetrics(1),rect.right,rect.bottom,origin.x,origin.y)


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
U.IsWindowVisible.argtypes=[W.HWND];U.IsWindowVisible.restype=W.BOOL
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
        self.version_label=APP_VERSION if not EDITION else APP_VERSION+' '+EDITION
        set_app_identity()
        if FENCE:
            from dialog_ui import register_panel_font
            register_panel_font()
        self.root=TkinterDnD.Tk();self.root.title('ID486模拟助手' if not EDITION else 'ID486模拟助手 · '+EDITION);self.root.geometry('640x660');self.root.resizable(False,False)
        self.root.configure(bg='#F4F1FC')
        self.root.iconbitmap(str(icon_path()))
        self.window_icons=WindowIcons(self.root)
        self.lock=threading.RLock();self.token=0;self.active=False;self.arming=False;self.closed=False
        self.capture_session=None;self.parallel_session=None
        self.pending=queue.Queue();self.ui=queue.Queue();self.gate=Gate();self.detector=Detector()
        base=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
        legacy_base=base
        if '--data-dir' in sys.argv:base=Path(sys.argv[sys.argv.index('--data-dir')+1])
        elif EDITION=='Fence':base=base/'Fence数据'
        elif EDITION=='PMC':base=base/'PMC数据'
        else:base=base/'数据'
        base=writable_data_directory(base, EDITION or 'V0.3')
        if '--data-dir' not in sys.argv and not (base/'settings.json').exists():
            import shutil
            for candidate_dir in (legacy_base/'PMC数据',legacy_base/'Fence数据',legacy_base):
                if (candidate_dir/'settings.json').is_file():
                    for name in ('settings.json','sound_settings.json','hotkey_settings.json'):
                        if (candidate_dir/name).is_file():
                            try:shutil.copy2(candidate_dir/name,base/name)
                            except OSError:pass
                    if (candidate_dir/'user_audio').is_dir():
                        try:shutil.copytree(candidate_dir/'user_audio',base/'user_audio',dirs_exist_ok=True)
                        except OSError:pass
                    break
        jsonl_only=(EDITION=='Fence')
        self.jsonl_log=None
        try:
            self.log=open(base/('monitor_events.jsonl' if jsonl_only else '运行记录.txt'),'a',encoding='utf-8',buffering=1)
            if not jsonl_only and FENCE:
                self.jsonl_log=open(base/'monitor_events.jsonl','a',encoding='utf-8',buffering=1)
        except OSError:
            base=Path(os.environ['LOCALAPPDATA'])/'TarkovHUDMonitor'/(EDITION or 'V0.3');base.mkdir(parents=True,exist_ok=True)
            self.log=open(base/('monitor_events.jsonl' if jsonl_only else '运行记录.txt'),'a',encoding='utf-8',buffering=1)
            if not jsonl_only and FENCE:
                self.jsonl_log=open(base/'monitor_events.jsonl','a',encoding='utf-8',buffering=1)
        self.journal=EventJournal(self.log,jsonl_only,jsonl_stream=self.jsonl_log);self.exit_enabled=False
        self.network=None;self.network_var=tk.BooleanVar(value=False)
        self.network_allowed=False;self.network_session_started=False;self.network_request_revision=0
        self.network_status=tk.StringVar(value='联网控制未开启');self.network_seconds=120;self.network_local_pve=False
        if NETWORK:
            from network_control import NetworkController
            self.network=NetworkController(base,Path(__file__).parent/'assets',self.event,lambda text:self.ui.put(('network_status',text)))
        self.keyboard_diagnostics=KeyboardDiagnostics(base/'keyboard_diagnostics',record=FENCE)
        self.event('keyboard_diagnostics_ready',available=self.keyboard_diagnostics.available,error=self.keyboard_diagnostics.error,pre_seconds=15,post_seconds=15,scope='monitor armed + foreground game PID; post-trigger 15s',temporary=True)
        self.config_path=base/'settings.json'
        self._loaded_preferences=load_settings(self.config_path)
        if NETWORK:
            self.network_allowed=self._loaded_preferences.get('network_allowed') is True
            self.network_local_pve=True # PvE application scope; the removed dialog is not a hidden gate.
            self.network_var.set(self.network_allowed)
        try:
            value=int(self._loaded_preferences.get('network_seconds',120))
            if 1<=value<=170:self.network_seconds=value
        except (ValueError,TypeError):pass
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
        if self.network_allowed:self.network_status.set(self.network_standby_text())
        self.key_label=tk.StringVar(value=self.key_name)
        self.key_hint=tk.StringVar(value=f'回到游戏后按 {self.key_name} 开启，再按一次停止')
        self.event('startup',hotkey=self.key_name,registered=bool(self.hotkeys),build=APP_BUILD,exit_policy='terminate_verified_game_with_close_fallback')
        self.status=tk.StringVar(value=f'已关闭 · 进入战局、HUD 可见后按 {self.key_name}')
        self.scores=tk.StringVar(value='等待开启')
        self.dry=tk.BooleanVar(value=self._loaded_preferences.get('dry_run',True) is not False)
        self.collect_enabled=False
        self.collect_var=tk.BooleanVar(value=False)
        self.diagnostic_status=tk.StringVar(value='截图默认关闭；手动开启后仅候选附近保存，最多2组 / 32MB')
        self.diagnostics=Diagnostics(base/'diagnostics',self.event)
        self.config_path=base/'settings.json'
        try:
            configured=int(load_settings(self.config_path)['delay_ms'])
            if not 50<=configured<=2000:raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):configured=300
        self.gate.delay_ms=configured
        self.delay_text=tk.StringVar(value=str(configured))
        try:
            hz=int(load_settings(self.config_path).get('capture_hz',30))
            if hz not in (15,30,60):raise ValueError()
        except (OSError,ValueError,TypeError):hz=30
        self.capture_hz=hz
        self.hz_text=tk.StringVar(value=str(hz))
        self.build_ui()
        self.event('preferences_restored',network_allowed=self.network_allowed,
            network_local_pve=self.network_local_pve,dry_run=self.runtime_dry,
            network_runtime_enabled=bool(self.network and self.network.enabled),monitoring_active=self.active)
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
        try:preferences=load_settings(self.config_path)
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

    def stop_all(self):
        self.cancel('手动停止')

    def network_standby_text(self):
        return '游戏/UU隔离保险待命 · 战局内按 '+self.key_name+' 开启'

    def stop_network_session(self):
        if not getattr(self,'network',None):return False
        if (self.network_session_started or self.network.enabled or getattr(self.network,'wanted',False)):
            self.network_request_revision=self.network.set_enabled(False)
            self.network_session_started=False
            self.ui.put(('network_status',dict(text='正在停止联网控制并恢复网络',revision=self.network_request_revision,
                                               enabled=True,phase='stopping')))
            return True
        elif self.network_allowed:
            self.ui.put(('network_status',dict(text=self.network_standby_text(),revision=self.network_request_revision,
                                               enabled=False,phase='standby')))
        return False

    def start_network_session(self,token,info):
        # Called only after this manually requested arming attempt passes the HUD gate.
        if token!=self.token or not self.active or self.closed:return
        if not self.network or not self.network_allowed:return
        if not self.network_local_pve:
            self.ui.put(('network_status',dict(text='联网控制待命 · 请先在设置中确认仅用于本地PvE',
                revision=self.network_request_revision,enabled=False,phase='standby')))
            return
        self.network_request_revision=self.network.set_enabled(True,self.network_seconds,self.network_local_pve,
            pid=info[1],monitor_token=token,combat_confirmed=True)
        self.network_session_started=True
        self.event('network_manual_start',monitor_token=token,pid=info[1],request_revision=self.network_request_revision)

    def toggle_network(self):
        with self.lock:self._toggle_network_permission()

    def _toggle_network_permission(self):
        if not self.network:return
        if self.network_var.get():
            self.network_allowed=False;self.network_var.set(False)
            if not self.stop_network_session():self.network_status.set('联网控制未开启')
            self.write_preferences(network_allowed=False,network_local_pve=True)
            self.event('network_permission_changed',allowed=False)
            return
        self.network_allowed=True;self.network_local_pve=True;self.network_var.set(True)
        self.network_status.set(self.network_standby_text())
        self.write_preferences(network_allowed=True,network_local_pve=True)
        self.event('network_permission_changed',allowed=True)

    def open_network_settings(self):
        # Compatibility for old callbacks: the field is now a binary toggle.
        self.toggle_network()

    def start_network_diagnostic(self,local_pve,external=False):
        if not FENCE or not self.network:return
        if self.active or self.arming or self.network_session_started:
            self.prep_feedback.set('请先按快捷键停止现有监控，再启动独立验证');return
        if not local_pve:
            self.prep_feedback.set('请先确认仅用于本地PvE的验证');return
        from network_control import find_game
        pid=find_game()
        if not pid:
            self.prep_feedback.set('未找到唯一的塔科夫进程，未启动验证');return
        self.network_request_revision=self.network.set_enabled(True,15,True,pid=pid,monitor_token=self.token,diagnostic=True,
            diagnostic_scope='game_external_control' if external else 'game_all_network')
        self.network_session_started=True
        self.network_status.set('正在启动15秒游戏及UU联网隔离验证')
        self.event('network_diagnostic_started',pid=pid,seconds=15,game_exit_enabled=False,scope='game_external_control' if external else 'game_all_network')
        if getattr(self,'network_window',None) and self.network_window.winfo_exists():self.network_window.destroy()

    def write_preferences(self,**changes):
        try:values=load_settings(self.config_path)
        except (OSError,ValueError):values={}
        values.update(changes)
        values.pop('exit_disabled',None) # retired duplicate switch; rehearsal is the only test mode
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
        # Rehearsal must work even if an unrelated numeric field is invalid.
        with self.lock:
            mode=self.dry.get()
            if mode!=self.runtime_dry:
                self.runtime_dry=mode
                if not mode and (self.active or self.arming):self.cancel('演练已关闭，请按快捷键重新开启实战监控')
                self.event('rehearsal_mode_changed',enabled=mode,monitoring_active=self.active)
                self.write_preferences(dry_run=mode)
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
        return True

    def persist_session(self):
        if not self.apply_runtime_settings():raise ValueError('invalid settings')
        self.write_preferences(profile_version=1,delay_ms=self.gate.delay_ms,capture_hz=self.capture_hz,
            collect_screenshots=self.collect_enabled,detailed_logs=self.detailed_logs,ui_theme=self.ui_theme,
            dry_run=self.dry.get(),hotkey_combo=list(self.hotkey_service.combo),hotkey_enabled=bool(self.hotkeys),
            hotkey_choices=[list(c) for c in self.main_ui.key_choices],sound_profile=self.sounds.profile())
        if NETWORK:self.write_preferences(network_seconds=self.network_seconds,network_allowed=self.network_var.get(),
            network_local_pve=self.network_local_pve)
        self.event('settings_saved',network_allowed=self.network_var.get(),
            network_local_pve=self.network_local_pve,dry_run=self.dry.get(),network_seconds=self.network_seconds)

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
        self.journal.write(kind,details,getattr(self,'detailed_logs',False))
        if kind=='diagnostic_saved':self.ui.put(('diagnostic_saved',details['path']))

    def cancel(self,reason):
        with self.lock:
            self.token+=1;self.active=False;self.arming=False;self.exit_enabled=False;self.gate.cancel()
            self.stop_network_session()
            if getattr(self,'keyboard_diagnostics',None):self.keyboard_diagnostics.pause()
            self.event('disarmed',reason=reason)
            self.ui.put(('status',f'已关闭 · {reason}'))



    def toggle(self):
        with self.lock:
            if self.sound_import_busy:
                self.sound_hint.set('音频转换完成后即可开启监控');return
            self.event('hotkey_received',key=self.key_name,active=self.active,arming=self.arming)
            if self.active or self.arming or self.network_session_started:
                self.cancel(self.key_name+' 手动关闭')
                return
            if len(self.hotkeys)!=1:return
            info=target()
            if not info:
                self.event('arm_rejected',reason='前台进程/窗口检查未通过')
                self.ui.put(('status','未开启：请将游戏置于主屏全屏/无边框并保持前台'));return
            self.exit_enabled=True
            self.token+=1;token=self.token;self.arming=True
            self.pending.put((token,info,self.dry.get()))
            self.ui.put(('status','正在检查当前 HUD，请保持游戏前台…'))

    def validate_observation_owner(self,token,info,engine,action,force_pause=False):
        """Reject a decision without consuming the armed session if focus moved.

        Must only run before sending an exit request. An accepted request remains
        terminal and can never reactivate monitoring through this helper.
        """
        with self.lock:
            if token!=self.token or not self.active or self.closed:return 'stopped'
            if not force_pause and target()==info:return 'current'
            self.gate.active=True
            engine.pause()
            if self.network:self.network.candidate_update(dict(action='cancel',reason='focus_changed'),monitor_token=token)
            self.event('observation_discarded_focus_changed',proposed_action=action,pid=info[1])
            self.event('focus_paused',source='decision_validation')
            if engine.transfer.active:
                self.ui.put(('transfer_status',dict(token=token,phase=engine.transfer.phase,resumed=False,paused=True)))
            else:self.ui.put(('status','监控中 · 已切出游戏，暂停判断；切回自动继续'))
            return 'paused'

    def record_exit_timing(self,engine,direct,fallback_origin=None):
        origins=[fallback_origin,getattr(getattr(engine,'gate',None),'missing_since',None),
                 getattr(getattr(engine,'isolation_candidate',None),'episode_first',None)]
        origin=min(x for x in origins if x is not None) if any(x is not None for x in origins) else None
        request=direct.get('request_monotonic_ns')
        elapsed=(request/1e9-origin)*1000 if request and origin is not None else None
        episode=getattr(engine,'isolation_candidate',None)
        first=getattr(episode,'diagnostic_first',None)
        episode_elapsed=(request/1e9-first)*1000 if request and first is not None else None
        self.event('death_exit_timing',pid=direct.get('pid'),first_full_monotonic=origin,
            request_monotonic_ns=request,suspicious_to_exit_ms=round(elapsed,3) if elapsed is not None else None,
            risk_limit_ms=1000,risk_exceeded=elapsed>1000 if elapsed is not None else None,
            diagnostic_episode_id=getattr(episode,'diagnostic_id',None),
            first_reviewed_monotonic=first,episode_to_exit_ms=round(episode_elapsed,3) if episode_elapsed is not None else None,
            episode_risk_exceeded=episode_elapsed>1000 if episode_elapsed is not None else None,
            episode_capture_gaps=getattr(episode,'diagnostic_gaps',None),
            meaning='observed_full_absence_to_exit_not_actual_death_onset')

    def parallel_report(self,record):
        # Completed/deferred attempt only; never write a file before the fast
        # path's exit request. Keep machine-readable proof outside the short UI log.
        record=dict(record,timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
                    unix_time_ns=time.time_ns(),recorded_monotonic_ns=time.perf_counter_ns())
        try:
            with (self.config_path.parent/'quick_check_events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
        except OSError:pass
        self.event('settlement_parallel_result',request_id=record.get('request_id'),
                   state=record.get('status'),reason=record.get('reason'),
                   read_to_visual_ms=record.get('read_to_finish_ms'))

    def parallel_death(self,token,info,engine,exit_target,capture,packet):
        # The normal death branch uses this same app lock and invalidates the
        # same token. Only one branch can accept and consume a live session.
        with self.lock:
            if token!=self.token or not self.active or self.closed:return False
            if not foreground_owner(info) or target()!=info or U.GetAsyncKeyState(0x1B)&0x8000:return False
            if packet['guard_epoch']!=capture.epoch:return False
            continuity=getattr(self,'hud_continuity',None)
            if continuity and continuity.capture is capture and continuity.veto(packet['frame_time'],packet['guard_epoch']):return False
            hint=dict(packet['hint'],full_frame=True)
            if not engine.settlement_fast_eligible(packet['frame_time'],packet['features'],
                    packet['menu_score'],packet['cue'],hint):return False
            dry=self.runtime_dry or not self.exit_enabled;watch=None
            if self.network:self.network.death_detected(not dry,monitor_token=token)
            decision_wall=time.time_ns()
            direct=None if dry else exit_target.terminate()
            if not dry and not (direct.get('accepted') or direct.get('state')=='already_exited'):
                self.event('settlement_parallel_exit_deferred',reason=direct.get('state'))
                return False # Ordinary path retains its validated fallback.
            self.gate.cancel();self.active=False;self.token+=1
            if not dry:
                watch=(exit_target.detach(),info[1],self.token,info[0],direct.get('request_monotonic_ns'))
            message='只记录：已检测到 UI 消失，未发送退出请求（独立快速检查）' if dry else '已请求结束游戏进程；独立快速检查退出确认中'
        # Accepted actions precede all logging, screenshots and UI notifications.
        if watch:threading.Thread(target=self.observe_exit,args=watch,daemon=True).start()
        self.event('death_decision',decision_wall_ns=decision_wall,source='parallel_settlement_visual',
                   analysis_ms=packet['proof'][-1][1]['probe_ms'])
        if direct:
            self.event('process_termination_request',**direct)
            self.record_exit_timing(engine,direct)
        read_to_exit=None if not direct else (direct['request_monotonic_ns']/1e9-hint['seen_monotonic'])*1000
        self.event('settlement_parallel_confirmed',request_id=hint['request_id'],log_lag_ms=hint.get('log_lag_ms'),
                   read_to_exit_ms=None if read_to_exit is None else round(read_to_exit,3),
                   frame_sequences=[item[0].sequence for item in packet['proof']],
                   frame_times=[item[0].captured_at for item in packet['proof']])
        self.event('trigger',dry_run=dry,result=message,delay_ms=self.gate.delay_ms)
        if getattr(self,'keyboard_diagnostics',None):
            self.keyboard_diagnostics.trigger(info[1],dict(candidate=input_snapshot('parallel_candidate'),dry_run=dry))
        if self.collect_enabled and self.diagnostics:
            self.diagnostics.enqueue_parallel(packet['proof'],info[1],dict(request_id=hint['request_id'],source='parallel_settlement_visual'))
        self.ui.put(('status','已关闭 · '+message))
        return True

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
            esc_audit=None
            continuity=None
            settlement_watch=None
            parallel_check=None;parallel_target=None
            engine=DecisionEngine(self.gate);extraction_guard=engine.extraction;exit_target=None
            isolation_candidate=IsolationCandidate();engine.isolation_candidate=isolation_candidate
            extraction_trace=deque(maxlen=80);last_trace_time=0.
            transfer_detector=TransferDetector();transfer_flow=engine.transfer;last_transfer_phase=None;last_transfer_scan=-100.;inventory_expanded=False;last_inventory_state=None;settings_expanded=False;last_geometry=None;left_was_down=False;force_full_after_focus=False
            region=(0,0,info[2],info[3]) # one initial full frame also checks an already-running transit countdown
            if hasattr(self.detector,"reset_layout"):self.detector.reset_layout()
            shape=(region[3],info[2])
            cap_ms=[];analysis_ms=[];esc_fast_ms=[];death_fast_ms=[];parallel_ui_ms=[];title_reuses=0;newer_frame_vetoes=0;attempts=0;new_frames=0
            window_start=time.perf_counter();cpu_start=time.process_time()
            def performance():
                nonlocal window_start,cpu_start,attempts,new_frames,cap_ms,analysis_ms,esc_fast_ms,death_fast_ms,parallel_ui_ms,title_reuses,newer_frame_vetoes
                wall=time.perf_counter()-window_start
                def percentile(values,p):
                    return sorted(values)[int((len(values)-1)*p)] if values else None
                if attempts:
                    self.event('performance',duration_s=round(wall,3),capture_region=list(region),capture_hz=hz,
                        attempts=attempts,new_frames=new_frames,checks_per_second=round(new_frames/max(wall,.001),2),
                        process_cpu_one_core_percent=round((time.process_time()-cpu_start)/max(wall,.001)*100,2),
                        capture_ms_p50=percentile(cap_ms,.50),capture_ms_p95=percentile(cap_ms,.95),
                        analysis_ms_p50=percentile(analysis_ms,.50),analysis_ms_p95=percentile(analysis_ms,.95),
                        esc_fast_frames=len(esc_fast_ms),esc_fast_ms_p50=percentile(esc_fast_ms,.50),
                        death_fast_frames=len(death_fast_ms),death_fast_ms_p50=percentile(death_fast_ms,.50),
                        parallel_ui_frames=len(parallel_ui_ms),parallel_ui_ms_p50=percentile(parallel_ui_ms,.50),
                        title_reuses=title_reuses,newer_frame_vetoes=newer_frame_vetoes)
                cap_ms=[];analysis_ms=[];esc_fast_ms=[];death_fast_ms=[];parallel_ui_ms=[];title_reuses=0;newer_frame_vetoes=0;attempts=0;new_frames=0
                window_start=time.perf_counter();cpu_start=time.process_time()
            try:
                import dxcam
                def capture_safe():
                    return ((self.active or self.arming) and token==self.token and not self.closed and foreground_owner(info)
                        and not bool(U.GetAsyncKeyState(0x1B)&0x8000)
                        and (last_geometry is None or geometry_signature(info)==last_geometry))
                camera=SharedCapture(AutoCapture(),hz,(0,0,info[2],info[3]),capture_safe)
                self.capture_session=camera
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
                    if hasattr(self.detector,'lock_current_layout'):self.detector.lock_current_layout()
                    self.active=True
                    exit_target=GameProcess(info[1])
                    self.event('exit_target_bound',pid=info[1],bound=bool(exit_target.handle),direct=exit_target.can_terminate,error=exit_target.error)
                    self.start_network_session(token,info)
                    if getattr(self,'keyboard_diagnostics',None):self.keyboard_diagnostics.start(info[1])
                    self.event('armed',arm_policy=confirmation.summary(),dry_run=dry,scores=scores,menu_score=menu_score,
                        delay_ms=self.gate.delay_ms,capture_hz=hz,capture_region=list(region),features=self.detector.last_features,menu_features=self.detector.last_menu_features,extraction_cues=getattr(self.detector,'last_extraction',{}))
                    self.ui.put(('status','监控中 · '+('只记录，不退出' if dry else '战斗 HUD 和菜单均消失并超过设定延时才退出')))
                if getattr(exit_target,'creation_unix',None) is not None:
                    try:
                        settlement_watch=SettlementWatch(exit_target.executable.parent/'Logs',exit_target.creation_unix,info[1],self.event,
                            on_request=lambda hint:self.network.end_request(hint,monitor_token=token) if self.network else None)
                        settlement_watch.start()
                    except (OSError,ValueError) as exc:self.event('settlement_watch_error',pid=info[1],error_type=type(exc).__name__)
                last_new=time.perf_counter();last_ui=0;paused=False;capture_waiting=False;settings_transition=False;next_tick=last_new
                settlement_probe_id=None
                title_detector=EscTitleDetector();esc_expanded=False;esc_requested=-100.;esc_down=False
                esc_audit=EscAudit(self.config_path.parent/'esc_diagnostics',self.event,images=self.collect_enabled)
                last_relative_reason=None;last_center_state=None;last_candidate_status=None
                steam_paused=False;steam_state=SteamOverlayState();steam_wait_reported=False;last_dialogue=False
                keyboard=getattr(self,'keyboard_diagnostics',None);esc_serial=getattr(keyboard,'escape_serial',0)
                esc_mode_reported=None;title_error_reported=False
                esc_fade=engine.esc_fade;hud_fade=engine.hud_fade;ui_transition_until=0.;last_pause_visible=False
                initial_transfer=transfer_detector.detect(getattr(self.detector,'menu_top',None),frame,(info[2],info[3]),getattr(self.detector,'layout',None))
                initial_result=transfer_flow.update(time.perf_counter(),initial_transfer,resume_hud(self.detector.last_features),ui_absent=not start_hud_evidence(self.detector.last_features))
                if initial_result['event']:self.event(initial_result['event'],phase=initial_result['phase'],cues=initial_transfer)
                capture_generation=camera.configuration_generation
                self.event("capture_backend_ready",**camera.configuration)
                last_geometry=geometry_signature(info)
                continuity=HudContinuity(camera,capture_safe,self.detector.layout or self.detector.layout_info,camera.epoch,on_suspect=lambda e:self.network.prepare_candidate(monitor_token=token,evidence=e) if self.network else None)
                self.hud_continuity=continuity
                top_height=capture_strip_height(info[2],info[3])
                expanded_height=min(info[3],max(top_height,round(info[3]*.47)))
                if settlement_watch:
                    parallel_target=GameProcess(info[1])
                    def parallel_owner():
                        return (token==self.token and self.active and not self.closed and not steam_paused and foreground_owner(info)
                            and target()==info and not bool(U.GetAsyncKeyState(0x1B)&0x8000)
                            and geometry_signature(info)==last_geometry)
                    parallel_check=ParallelSettlementCheck(camera,settlement_watch,FastProbe(),parallel_owner,
                        lambda packet,fast_camera=camera:self.parallel_death(token,info,engine,parallel_target,fast_camera,packet),self.parallel_report)
                    context=engine.settlement_context();context.update(active=True,observed_at=camera.capture_time,
                        last_combat=camera.capture_time,guard_epoch=camera.delivered.guard_epoch,
                        layout=dict(self.detector.layout),full_region=(0,0,info[2],info[3]))
                    parallel_check.publish(context);parallel_check.start()
                    self.parallel_session=parallel_check
                def collect(phase,stamp):
                    if diagnostic and self.collect_enabled and hasattr(self.detector,'last_top'):
                        evidence_frame=frame if FENCE and frame is not None and frame.shape[:2]==(info[3],info[2]) else self.detector.last_top
                        if FENCE and evidence_frame is self.detector.last_top:return
                        if frame is not None and frame.shape[:2]==(info[3],info[2]):
                            diagnostic.push_scene(frame,self.detector.last_top,getattr(self.detector,'menu_top',None),
                                dict(frame_sequence=camera.delivered.sequence,frame_monotonic=camera.delivered.captured_at,
                                    frame_wall_ns=camera.delivered.wall_ns,guard_epoch=camera.delivered.guard_epoch,
                                    capture_safe=camera.delivered.safe,full_size=[info[2],info[3]],phase=phase,
                                    layout=dict(self.detector.layout or self.detector.layout_info),
                                    features=dict(self.detector.last_features),menu_features=dict(self.detector.last_menu_features),
                                    extraction=dict(getattr(self.detector,'last_extraction',{})),
                                    relative=dict(getattr(self.detector,'last_relative_fade',{})),build=BUILD),
                                candidate_world=getattr(self.detector,'last_candidate_world_gray',None))
                        diagnostic.push(evidence_frame,dict(monotonic=stamp,wall_time=time.time()-(time.perf_counter()-stamp),phase=phase,game_pid=info[1],game_hwnd=info[0],
                            features=dict(self.detector.last_features),menu_features=dict(self.detector.last_menu_features),
                            delay_ms=self.gate.delay_ms,capture_hz=hz,full_size=[info[2],info[3]],
                            esc_capture_expanded=esc_expanded,capture_region=list(region),capture_source=camera.delivered.provenance,
                            absence_ms_before_decision=0 if self.gate.missing_since is None else (stamp-self.gate.missing_since)*1000))
                while True:
                    with self.lock:
                        if not self.active or token!=self.token or self.closed:break
                        if hz!=self.capture_hz:
                            hz=self.capture_hz;interval=1/hz;next_tick=time.perf_counter()
                            camera.set_hz(hz)
                            self.gate.max_observation_gap=max(.12,2.5/hz)
                        wanted=self.diagnostics if self.collect_enabled else None
                        if esc_audit:esc_audit.images=self.collect_enabled
                        if wanted is not diagnostic:
                            if diagnostic:diagnostic.reset()
                            diagnostic=wanted
                            if diagnostic:diagnostic.reset()
                    wait=next_tick-time.perf_counter()
                    if wait>0:
                        if settlement_watch:
                            settlement_watch.wake.wait(wait);settlement_watch.wake.clear()
                        else:time.sleep(wait)
                    next_tick=max(next_tick+interval,time.perf_counter())
                    current_target=target()
                    same_owner=foreground_owner(info)
                    signature=geometry_signature(info) if same_owner else None
                    if same_owner and signature is not None and last_geometry is not None and signature!=last_geometry:
                        camera.invalidate()
                        engine.begin_display_switch(time.perf_counter(),'native_geometry_changed')
                        self.event('display_geometry_changed',previous=last_geometry,current=signature)
                        last_geometry=signature
                    if current_target is not None and current_target[:2]==info[:2] and current_target[2:]!=info[2:]:
                        previous_size=info[2:];info=current_target
                        engine.begin_display_switch(time.perf_counter(),'native_capture_size_changed')
                        self.detector.reset_layout()
                        top_height=capture_strip_height(info[2],info[3]);expanded_height=min(info[3],max(top_height,round(info[3]*.47)))
                        if parallel_check:parallel_check.close();parallel_check=None
                        if settlement_watch:settlement_watch.discard()
                        if continuity:continuity.close()
                        if camera:camera.release()
                        camera=SharedCapture(AutoCapture(),hz,(0,0,info[2],info[3]),capture_safe)
                        self.capture_session=camera
                        continuity=HudContinuity(camera,capture_safe,on_suspect=lambda e:self.network.prepare_candidate(monitor_token=token,evidence=e) if self.network else None)
                        self.hud_continuity=continuity
                        if settlement_watch:
                            parallel_check=ParallelSettlementCheck(camera,settlement_watch,FastProbe(),parallel_owner,
                                lambda packet,fast_camera=camera:self.parallel_death(token,info,engine,parallel_target,fast_camera,packet),self.parallel_report)
                            parallel_check.start()
                            self.parallel_session=parallel_check
                        last_new=time.perf_counter()
                        self.event('capture_geometry_recreated',previous_size=previous_size,current_size=info[2:])
                    if current_target!=info:
                        self.gate.missing_since=None;self.gate.missing=0
                        if not paused:
                            engine.pause()
                            extraction_guard.reset()
                            extraction_trace.clear()
                            if diagnostic:diagnostic.reset()
                            self.event('focus_paused')
                            if transfer_flow.active:self.ui.put(('transfer_status',dict(token=token,phase=transfer_flow.phase,resumed=False,paused=True)))
                            else:self.ui.put(('status','监控中 · 已切出游戏，暂停判断；切回自动继续'))
                            paused=True
                        time.sleep(.05);last_new=time.perf_counter();continue
                    if paused:
                        force_full_after_focus=True
                        esc_fade.reset();hud_fade.reset();ui_transition_until=time.perf_counter()+.8;last_pause_visible=False
                        self.event('focus_resumed')
                        if transfer_flow.active:self.ui.put(('transfer_status',dict(token=token,phase=transfer_flow.phase,resumed=False)))
                        else:self.ui.put(('status','监控中 · 已回到游戏，继续判断'))
                        paused=False
                    # Escape only expands observation; it never means 'menu is present'.
                    keyboard=getattr(self,'keyboard_diagnostics',None)
                    serial=getattr(keyboard,'escape_serial',esc_serial)
                    key_down=bool(U.GetAsyncKeyState(0x1B)&0x8000)
                    if (serial!=esc_serial and getattr(keyboard,'escape_pid',None)==info[1]) or (key_down and not esc_down):
                        esc_expanded=True;esc_requested=time.perf_counter();ui_transition_until=esc_requested+.8
                        esc_fade.reset();hud_fade.reset()
                        self.event('esc_capture_requested',source='keyboard_edge')
                    esc_serial=serial;esc_down=key_down
                    if last_pause_visible and U.GetAsyncKeyState(0x01)&0x8000:ui_transition_until=time.perf_counter()+.8
                    scan_transfer=time.perf_counter()-last_transfer_scan>=(.1 if transfer_flow.active else .2)
                    log_capture=bool(settlement_watch and settlement_watch.peek())
                    region=(0,0,info[2],info[3] if steam_paused or last_dialogue or log_capture or force_full_after_focus or inventory_expanded or settings_expanded or engine.settings_active or engine.fade_pending is not None or scan_transfer or time.perf_counter()<self.detector.extraction_full_until or self.gate.missing_since is not None or (continuity and continuity.candidate) or self.detector.hsv_watch.started is not None else expanded_height if esc_expanded else top_height)
                    if scan_transfer:last_transfer_scan=time.perf_counter()
                    shape=(region[3],info[2])
                    begin=time.perf_counter()
                    frame=camera.grab(region=region,copy=False)
                    captured=time.perf_counter();now=camera.capture_time if frame is not None else captured
                    attempts+=1;cap_ms.append(camera.delivered.capture_ms if frame is not None else (captured-begin)*1000)
                    if camera.configuration_generation!=capture_generation:
                        capture_generation=camera.configuration_generation
                        engine.pause();extraction_trace.clear()
                        if diagnostic:diagnostic.reset()
                        if settlement_watch:settlement_watch.discard()
                        if parallel_check:parallel_check.publish(dict(active=False))
                        if hasattr(self.detector,'reset_layout'):self.detector.reset_layout()
                        self.event('capture_color_changed',**camera.configuration)
                        self.ui.put(('status','监控中 · 显示器色彩已变化，重新确认画面'))
                        continue

                    if frame is None:
                        transfer_wait_event=transfer_flow.advance_wait(now)
                        if transfer_wait_event:
                            self.event(transfer_wait_event,phase=transfer_flow.phase,source='previously_observed_absent_frame')
                            self.ui.put(('transfer_status',dict(token=token,phase=transfer_flow.phase,resumed=False,long_wait=transfer_wait_event=='transfer_wait_long')))
                            last_transfer_phase=transfer_flow.phase
                        if now-last_new>.75 and not capture_waiting:
                            capture_waiting=True
                            self.event('capture_waiting',no_new_frame_s=round(now-last_new,3))
                            if transfer_flow.active:self.ui.put(('transfer_status',dict(token=token,phase=transfer_flow.phase,resumed=False,stalled=True)))
                            else:self.ui.put(('status','监控中 · 暂无新画面，等待采集恢复'))
                        if now-window_start>=5:performance()
                        continue
                    if capture_waiting:
                        self.event('capture_resumed',no_new_frame_s=round(now-last_new,3))
                        if transfer_flow.active:self.ui.put(('transfer_status',dict(token=token,phase=transfer_flow.phase,resumed=False)))
                        else:self.ui.put(('status','监控中 · 采集已恢复，继续判断'))
                        capture_waiting=False
                    new_frames+=1;last_new=now
                    if U.GetForegroundWindow()!=info[0]:
                        extraction_guard.reset()
                        self.gate.missing_since=None;self.gate.missing=0
                        if diagnostic:diagnostic.reset()
                        continue
                    if frame.shape[:2]!=shape:
                        self.cancel('采集区域尺寸改变');break
                    force_full_after_focus=False
                    begin=time.perf_counter()
                    # Settings can change the viewport without changing capture dimensions.
                    # Reacquire layout while guarded; a stale viewport must not imply death.
                    if (settings_transition or transfer_flow.protected) and hasattr(self.detector,'reset_layout'):self.detector.reset_layout()
                    self.detector.relative_context=engine.relative_context(now,camera.delivered.guard_epoch,
                        transition=bool(now<ui_transition_until or steam_paused or last_dialogue or settings_transition or
                                        settings_expanded or last_pause_visible or not camera.delivered.safe))
                    title_ticket=None;transfer_ticket=None;title_precomputed=None;transfer_precomputed=None
                    speculative_ms=0.;spec_transfer_ms=0.;title_reused=False;transfer_reused=False
                    review_layout=self.detector.layout or self.detector.layout_info
                    if continuity and review_layout:
                        continuity.set_layout(review_layout,camera.delivered.guard_epoch)
                        if (frame.shape[0]>=info[3] and not steam_paused and not last_dialogue and
                            not bool(last_inventory_state and last_inventory_state[0]) and not transfer_flow.active and
                            not engine.settings_active and not settings_expanded and not last_pause_visible and now>=ui_transition_until):
                            light=continuity.inspect(frame,review_layout)
                            if not light['hud'] and not light['menu'] and FastProbe.possible_title(frame,review_layout):
                                title_ticket=TitleTicket(title_detector,frame,(info[2],info[3]),review_layout)
                                early_menu=light['menu_top'];early_layout=dict(review_layout)
                                transfer_ticket=ReviewTicket(lambda:transfer_detector.detect(early_menu,frame,(info[2],info[3]),early_layout),review_layout,frame)
                    try:
                        scores,menu_score=self.detector.analyze(frame,full_size=(info[2],info[3]))
                    finally:
                        # Drain the two jobs before any following frame can use these actors.
                        try:
                            if title_ticket:title_precomputed,speculative_ms=title_ticket.finish()
                        finally:
                            if transfer_ticket:transfer_precomputed,spec_transfer_ms=transfer_ticket.finish()
                    central=getattr(self.detector,'last_extraction',{}).get('center_timer',{})
                    if central.get('observed'):
                        center_state=(bool(central.get('present')),central.get('value'))
                        if center_state!=last_center_state and (center_state[0] or (last_center_state and last_center_state[0])):
                            self.event('center_extraction_countdown',value=central.get('value'),confidence=central.get('confidence'),
                                reason=central.get('reason'),roi=central.get('roi'),mapping=central.get('mapping'),
                                frame_sequence=camera.delivered.sequence,frame_wall_ns=camera.delivered.wall_ns,
                                evidence=central)
                        last_center_state=center_state
                    relative=getattr(self.detector,'last_relative_fade',{})
                    relative_reason=(relative.get('dark_trend') or relative.get('hsv') or relative).get('reason')
                    if (not start_hud_evidence(self.detector.last_features) and menu_score<.55 and
                        relative_reason not in (None,'not_enabled') and relative_reason!=last_relative_reason):
                        self.event('relative_reference_status',reason=relative_reason,evidence=relative)
                        last_relative_reason=relative_reason
                    detector_ms=(time.perf_counter()-begin)*1000
                    if getattr(self.detector,"last_parallel",{}).get("used"):parallel_ui_ms.append(self.detector.last_parallel["wall_ms"])
                    if getattr(self.detector,"last_esc_fast",{}).get("used"):esc_fast_ms.append(detector_ms)
                    death_fast=bool(getattr(self.detector,"last_death_fast",{}).get("used"))
                    if death_fast:death_fast_ms.append(detector_ms)
                    if continuity:
                        continuity.set_layout(self.detector.layout or self.detector.layout_info,camera.delivered.guard_epoch)
                    veto=continuity.veto(now,camera.delivered.guard_epoch) if continuity else None
                    if camera.epoch!=camera.delivered.guard_epoch or (veto and not start_hud_evidence(self.detector.last_features) and menu_score<.55):
                        engine.clear_absence();engine.log_frames=0;engine.log_frame_time=None
                        if parallel_check:parallel_check.publish(dict(active=False))
                        newer_frame_vetoes+=1
                        self.event('frame_review_veto',reason='capture_epoch_changed' if camera.epoch!=camera.delivered.guard_epoch else 'newer_positive_ui',evidence=veto)
                        force_full_after_focus=True
                        continue
                    interaction=getattr(self.detector,'last_interaction',{})
                    overlay=steam_state.update(now,interaction.get('steam'),
                        confirmed_game_ui(self.detector.last_features,self.detector.last_menu_features))
                    steam_paused=steam_state.paused
                    if overlay in ('paused_new','paused','waiting'):
                        if overlay=='paused_new':
                            steam_wait_reported=False;engine.pause();camera.invalidate()
                            if settlement_watch:settlement_watch.discard()
                            if parallel_check:parallel_check.publish(dict(active=False))
                            self.event('steam_overlay_paused',evidence=interaction)
                            self.ui.put(('status','监控中 · Steam界面已打开，暂停判断；关闭后自动继续'))
                        if overlay=='waiting' and not steam_wait_reported:
                            steam_wait_reported=True
                            self.event('steam_overlay_waiting')
                            self.ui.put(('overlay_waiting','监控中 · Steam画面暂未确认，保持暂停；回到游戏后自动恢复'))
                        collect('steam_overlay',now)
                        analysis_ms.append((time.perf_counter()-begin)*1000)
                        if now-window_start>=5:performance()
                        continue
                    if overlay=='resumed':
                        steam_wait_reported=False;engine.pause();camera.invalidate()
                        if settlement_watch:settlement_watch.discard()
                        self.event('steam_overlay_resumed')
                        self.ui.put(('status','监控中 · Steam界面已关闭，恢复画面判断'))
                    dialogue=bool(interaction.get('btr'))
                    if dialogue!=last_dialogue:
                        self.event('btr_dialogue_state',present=dialogue,evidence=interaction)
                    last_dialogue=dialogue
                    combat=self.detector.last_features['combat_score']
                    settings_data=self.detector.last_menu_features.get('settings_ui',{})
                    if self.detector.last_menu_features.get('settings',0.)>=.55 or self.detector.last_menu_features.get('pause',0.)>=.55:settings_expanded=True
                    elif start_hud_evidence(self.detector.last_features) and menu_score<.55:settings_expanded=False
                    mouse_down=bool(U.GetAsyncKeyState(0x01)&0x8000)
                    if mouse_down and not left_was_down and settings_data.get('apply_box'):
                        cursor=W.POINT()
                        if U.GetCursorPos(C.byref(cursor)):
                            x,y,x2,y2=settings_data['apply_box']
                            settings_data['apply_clicked']=x<=cursor.x<=x2 and y<=cursor.y<=y2
                    left_was_down=mouse_down
                    inventory_evidence=getattr(self.detector,'last_inventory',{})
                    inventory_visible=inventory_evidence.get('present',False) or self.detector.last_menu_features.get('inventory',0.)>=.55
                    if inventory_visible or (combat<=.22 and menu_score<.55):inventory_expanded=True
                    elif resume_hud(self.detector.last_features) and menu_score<.55:inventory_expanded=False
                    inventory_state=(inventory_visible,inventory_evidence.get('popup_count',0),inventory_evidence.get('bottom_slots',0),inventory_evidence.get('equipment_panel',{}).get('present',False))
                    if inventory_state!=last_inventory_state:
                        self.event('inventory_visual_state',present=inventory_visible,evidence=inventory_evidence,full_capture=inventory_expanded)
                        last_inventory_state=inventory_state

                    if getattr(self.detector,'layout_recovery',None):
                        self.event('hud_layout_corrected',evidence=self.detector.layout_recovery)
                        engine.layout_changed()
                    settlement_hint=settlement_watch.peek() if settlement_watch else None
                    if settlement_hint:
                        settlement_hint.update(full_frame=frame.shape[0]>=info[3]-2,
                            menu_intent=bool(now<ui_transition_until or last_pause_visible or settings_expanded))
                    fast_log_candidate=engine.settlement_fast_eligible(now,self.detector.last_features,menu_score,
                        getattr(self.detector,'last_extraction',{}),settlement_hint)
                    if fast_log_candidate and settlement_probe_id!=settlement_hint['request_id']:
                        settlement_probe_id=settlement_hint['request_id']
                        self.event('settlement_fast_probe',request_id=settlement_probe_id,
                            source_timestamp=settlement_hint['source_timestamp'],log_lag_ms=settlement_hint['log_lag_ms'])
                    transfer_started=time.perf_counter()
                    if fast_log_candidate or (death_fast and not transfer_flow.active):transfer_cue={}
                    elif transfer_ticket and transfer_ticket.matches(getattr(self.detector,'layout',None),frame) and np.array_equal(early_menu,self.detector.menu_top):
                        transfer_cue=transfer_precomputed;transfer_reused=True
                    else:transfer_cue=transfer_detector.detect(getattr(self.detector,'menu_top',None),frame,(info[2],info[3]),getattr(self.detector,'layout',None))
                    transfer_ms=(time.perf_counter()-transfer_started)*1000
                    if transfer_cue.get('menu'):menu_score=max(menu_score,1.)
                    base_menu=menu_score
                    audit_base=dict(frame_sequence=camera.delivered.sequence,frame_wall_ns=camera.delivered.wall_ns,
                        frame_monotonic=now,capture_region=list(region),frame_size=[frame.shape[1],frame.shape[0]],
                        full_size=list(info[2:]),layout=dict(self.detector.layout or self.detector.layout_info),
                        base_menu=base_menu,pause_left=self.detector.last_menu_features.get('pause_left'),
                        pause_right=self.detector.last_menu_features.get('pause_right'),
                        combat_score=combat,esc_serial=esc_serial,esc_expanded=esc_expanded)
                    audit_base.update(analysis_started=begin,detector_ms=round(detector_ms,3),transfer_ms=round(transfer_ms,3))
                    audit_base.update(relative_fade=dict(getattr(self.detector,"last_relative_fade",{})),death_fast=dict(getattr(self.detector,"last_death_fast",{})),esc_fast=dict(getattr(self.detector,"last_esc_fast",{})),pid=info[1],monitor_token=token,guard_epoch=camera.delivered.guard_epoch,
                        delay_ms=self.gate.delay_ms,capture_hz=hz,
                        absence_before_step_ms=self.gate.absence_observed*1000,
                        missing_since_before_step=self.gate.missing_since)
                    if not esc_expanded and combat<=.22 and menu_score<.55 and not transfer_flow.protected and not fast_log_candidate:
                        esc_expanded=True;esc_requested=now;force_full_after_focus=True
                        keep_full=full_frame_can_continue(frame.shape,info[2:],safe=camera.delivered.safe,
                            epoch_match=camera.delivered.guard_epoch==camera.epoch,owner_ok=foreground_owner(info),
                            transition=bool(now<ui_transition_until or settings_transition or engine.settings_active),
                            protected=bool(transfer_flow.protected))
                        if keep_full:
                            # Finish the remaining title/guard checks below on THIS
                            # frame. It is not a one-frame authorization to exit.
                            self.event('full_review_frame_retained',frame_sequence=camera.delivered.sequence,
                                capture_region=list(region),remaining_title_review=True)
                            audit_base['full_review_frame_retained']=True
                        else:
                            engine.clear_absence()
                            preserve=scope_expansion_can_preserve(frame.shape,info[2:],safe=camera.delivered.safe,
                                epoch_match=camera.delivered.guard_epoch==camera.epoch,owner_ok=foreground_owner(info),
                                transition=bool(now<ui_transition_until or settings_transition or engine.settings_active),
                                protected=bool(transfer_flow.protected))
                            if preserve:
                                camera.request_fresh_full()
                                self.detector.prepare_full_review()
                            else:camera.invalidate()
                            if parallel_check:parallel_check.publish(dict(active=False))
                            self.event('esc_capture_requested',reason='awaiting_fresh_expanded_frame',source='unknown_ui',reference_preserved=preserve)
                            esc_audit.submit(dict(audit_base,kind='title_check',called=False,reason='awaiting_fresh_expanded_frame',reference_preserved=preserve),force=True)
                            analysis_ms.append((time.perf_counter()-begin)*1000)
                            if now-window_start>=5:performance()
                            continue
                    title_result=None
                    title_reason='certified_black_scene' if death_fast else title_skip_reason(esc_expanded,combat,menu_score,transfer_flow.protected,fast_log_candidate)
                    title_called=title_reason is None
                    if title_called:
                        try:
                            if title_ticket and title_ticket.matches(getattr(self.detector,'layout',None),frame):
                                title_result=title_precomputed;title_reused=True
                            else:title_result=title_detector.detect(frame,(info[2],info[3]),getattr(self.detector,'layout',None))
                            title_reason=title_result['reason']
                            self.detector.last_menu_features['esc_title']=title_result
                            if title_reason=='needs_expanded_capture':
                                engine.clear_absence();force_full_after_focus=True
                                esc_audit.submit(dict(audit_base,kind='title_check',called=True,reason=title_reason,title=title_result),force=True)
                                continue
                            if title_result['present']:
                                menu_score=max(menu_score,1.);settings_expanded=True
                                if hasattr(self.detector,'observe_settings'):self.detector.observe_settings=True
                        except Exception as exc:
                            esc_audit.submit(dict(audit_base,kind='title_check',called=True,reason='model_exception',error_type=type(exc).__name__),force=True)
                            if not title_error_reported:self.event('esc_title_error',reason=str(exc));title_error_reported=True
                            self.cancel('ESC标题检测失败，已停止监控，请查看日志');break
                    if esc_expanded and combat_hud_evidence(self.detector.last_features) and now-esc_requested>.35:
                        esc_expanded=False
                        self.event('esc_capture_restored',layout=self.detector.last_features.get('layout'))
                    if esc_expanded:
                        state='menu_visible' if menu_score>=.55 else 'ui_absent'
                        if state!=esc_mode_reported:
                            self.event('esc_visual_state',state=state,combat_score=combat,menu_score=menu_score,title=title_result)
                            esc_mode_reported=state
                    else:esc_mode_reported=None
                    if title_reused:title_reuses+=1
                    audit_base.update(parallel_ui=dict(getattr(self.detector,'last_parallel',{})),
                        title_speculated=title_ticket is not None,title_reused=title_reused,title_speculative_ms=round(speculative_ms,3),
                        transfer_reused=transfer_reused,transfer_speculative_ms=round(spec_transfer_ms,3))
                    veto=continuity.veto(now,camera.delivered.guard_epoch) if continuity else None
                    if camera.epoch!=camera.delivered.guard_epoch or (veto and not start_hud_evidence(self.detector.last_features) and menu_score<.55):
                        engine.clear_absence();engine.log_frames=0;engine.log_frame_time=None
                        if parallel_check:parallel_check.publish(dict(active=False))
                        newer_frame_vetoes+=1
                        self.event('frame_review_veto',reason='capture_epoch_changed' if camera.epoch!=camera.delivered.guard_epoch else 'newer_positive_ui',evidence=veto)
                        force_full_after_focus=True;continue
                    last_pause_visible=(self.detector.last_menu_features.get('pause',0.)>=.55
                                        or bool((title_result or {}).get('present'))
                                        or self.detector.last_menu_features.get('settings',0.)>=.55)
                    previous_missing=self.gate.missing_since
                    with self.lock:
                        if token!=self.token or not self.active or self.closed:break
                        outcome=engine.step(now,scores,self.detector.last_features,menu_score,self.detector.last_menu_features,
                            getattr(self.detector,'last_top',None),transfer_cue,getattr(self.detector,'last_extraction',{}),
                            suppress_fade=now<ui_transition_until,menu_top=getattr(self.detector,'menu_top',None),
                            settlement_hint=settlement_hint if fast_log_candidate else None,world=getattr(self.detector,'last_world_gray',None),
                            black_confirmed=getattr(self.detector,'last_death_fast',{}).get('reason')=='certified_black_scene')
                        if esc_expanded or outcome['action'] in ('death','uncertain'):
                            esc_audit.submit(dict(audit_base,kind='title_check',called=title_called,reason=title_reason,
                                title=title_result,action=outcome['action'],decision_events=outcome['events'],
                                final_menu=outcome['menu_score'],esc_fade=outcome.get('early_esc_fade'),
                                observed_absence_ms=self.gate.absence_observed*1000),
                                image=title_detector.last_input if title_called else None,
                                force=outcome['action'] in ('death','uncertain'))
                        if parallel_check:
                            context=engine.settlement_context();context.update(guard_epoch=camera.delivered.guard_epoch,
                                layout=dict(self.detector.layout or self.detector.layout_info),full_region=(0,0,info[2],info[3]),
                                menu_intent=bool(now<ui_transition_until or last_pause_visible or settings_expanded))
                            parallel_check.publish(context)
                    ownership=self.validate_observation_owner(token,info,engine,outcome['action'])
                    if ownership=='stopped':break
                    if ownership=='paused':paused=True;continue
                    transfer_result=outcome['transfer'];menu_score=outcome['menu_score'];settings_transition=engine.settings_active
                    # Network-only candidate: every input already passed complete UI,
                    # title, ownership and newer-frame vetoes. Never changes Gate.
                    ccontext=engine.isolation_context()
                    full_review=(frame.shape[1]==info[2] and frame.shape[0]==info[3] and
                        camera.delivered.safe and camera.delivered.guard_epoch==camera.epoch and
                        0<=time.perf_counter()-now<=.65)
                    relative_current=getattr(self.detector,'last_relative_fade',{}).get('dark_trend',{}).get('current',{})
                    candidate_result=reviewed_candidate(isolation_candidate,now,camera.delivered.sequence,camera.delivered.guard_epoch,
                        valid=full_review,features=self.detector.last_features,menu_score=outcome['menu_score'],
                        context=ccontext,cue=getattr(self.detector,'last_extraction',{}),action=outcome['action'],
                        photometry=relative_current,world=getattr(self.detector,'last_candidate_world_gray',None),menu_intent=bool(now<ui_transition_until or last_pause_visible or
                            settings_expanded or last_dialogue or steam_paused))
                    candidate_status=(candidate_result['reason'],ccontext.get('transition_basis'),ccontext.get('countdown_observed'))
                    if (candidate_status!=last_candidate_status and outcome['action'] not in ('death','extraction') and
                        not start_hud_evidence(self.detector.last_features)):
                        self.event('network_candidate_status',pid=info[1],frame_sequence=camera.delivered.sequence,
                            evidence=dict(candidate_result),menu_score=outcome['menu_score'],full_review=full_review)
                    last_candidate_status=candidate_status
                    if candidate_result['action']=='isolate':
                        candidate_result.update(review_completed_wall_ns=time.time_ns(),
                            first_full_wall_ns=camera.delivered.wall_ns-round((now-candidate_result['first_monotonic'])*1e9),
                            suspicious_to_candidate_ms=round((time.perf_counter()-candidate_result['first_monotonic'])*1000,3))
                    if candidate_result['action']!='none' and outcome['action']!='death':
                        self.event('network_candidate_visual',pid=info[1],frame_sequence=camera.delivered.sequence,
                            frame_wall_ns=camera.delivered.wall_ns,evidence=candidate_result)
                        if self.network:self.network.candidate_update(candidate_result,monitor_token=token)

                    if self.network and self.network.enabled:
                        self.network.normal_observation(monitor_token=token,observed_at=now,
                            sequence=camera.delivered.sequence,epoch=camera.delivered.guard_epoch,
                            full_review=bool(full_review and camera.delivered.safe and camera.delivered.guard_epoch==camera.epoch),
                            full_hud=bool(combat_hud_evidence(self.detector.last_features) and menu_score<.55),
                            verified_transfer=bool(transfer_flow.saw_loading and transfer_flow.protected and transfer_flow.phase!='return_unobserved'))
                        cue=getattr(self.detector,'last_extraction',{})
                        timer_seen=cue.get('title_score',0)>=.8 and cue.get('timer_state') in ('zero','nonzero')
                        if transfer_flow.active or timer_seen:self.network.scene(settled=True,monitor_token=token)
                    self.detector.last_menu_features.update(transfer=dict(cues=transfer_cue,state=transfer_result,fast_observation=outcome.get('fast_transit_watch',False)),
                        early_esc_fade=outcome.get('early_esc_fade',{}),hud_preserving_fade=outcome.get('hud_preserving_fade',{}))
                    settlement_confirmation=None
                    for record in outcome['events']:
                        kind=record['event'];detail={k:v for k,v in record.items() if k!='event'}
                        if kind=='settlement_visual_confirmed':settlement_confirmation=detail;continue
                        self.event(kind,**detail,features=dict(self.detector.last_features))
                        if kind=='display_switch_guard_started':self.ui.put(('status','监控中 · 已确认显示参数切换，正在重新定位画面'))
                        elif kind=='settings_transition_resumed':self.ui.put(('status','监控中 · 已重新定位战斗HUD，继续判断'))
                        elif kind=='transfer_hint_cancelled':
                            self.ui.put(('transfer_status',dict(token=token,phase='idle',resumed=False,hint_cancelled=True)))
                            last_transfer_phase=None
                        elif kind in ('transfer_manual_rearm_needed','transfer_return_unobserved'):self.ui.put(('transfer_status',dict(token=token,phase=transfer_flow.phase,resumed=False,manual=True)))
                    if transfer_flow.active and transfer_result['phase']!=last_transfer_phase:
                        self.ui.put(('transfer_status',dict(token=token,phase=transfer_result['phase'],resumed=False)))
                        last_transfer_phase=transfer_result['phase']
                    if transfer_result['play_enabled']:
                        if self.network:self.network.scene(resumed=True,monitor_token=token)
                        if hasattr(self.detector,'lock_current_layout'):self.detector.lock_current_layout()
                        self.ui.put(('transfer_status',dict(token=token,phase='idle',resumed=True)))
                        last_transfer_phase=None;esc_expanded=False
                    if outcome['action']=='uncertain':
                        self.cancel(outcome['reason']);break
                    if outcome['action'] in ('transfer_wait','settings_wait'):
                        collect(outcome['action'],now)
                        analysis_ms.append((time.perf_counter()-begin)*1000)
                        if now-window_start>=5:performance()
                        if outcome['action']=='transfer_wait':time.sleep(max(0.,.1-(time.perf_counter()-begin)))
                        continue
                    collect('monitoring',now)
                    analysis_ms.append((time.perf_counter()-begin)*1000)
                    if now-last_ui>.2:
                        self.ui.put(('scores',f"战局特征 {self.detector.last_features['combat_score']:.0%} · 人形轮廓 {scores[0]:.0%} · 快捷栏 {scores[1]:.0%} · 菜单 {menu_score:.0%}"))
                        last_ui=now
                    with self.lock:
                        if token!=self.token or not self.active or self.closed:break
                        extracted=outcome['action']=='extraction'
                        if now-last_trace_time>=.10 or extracted:
                            extraction_trace.append(dict(monotonic=now,combat_score=combat,menu_score=menu_score,
                                cues=dict(getattr(self.detector,'last_extraction',{})),guard=extraction_guard.trace(now)))
                            last_trace_time=now
                        if extracted:
                            if self.network:self.network.scene(settled=True,monitor_token=token)
                            self.event('extraction_transition',evidence=outcome.get('evidence'),extraction_trace=list(extraction_trace))
                            self.cancel('识别到撤离渐黑过渡，停止监控（未执行退出）');break
                        fired=outcome['action']=='death'
                        if previous_missing is not None and self.gate.missing_since is None and not fired:
                            self.event('absence_reset',combat_score=self.detector.last_features['combat_score'],menu_score=menu_score)
                        if fired:
                            late_veto=continuity.veto(now,camera.delivered.guard_epoch,menu_kind(self.detector.last_menu_features) if menu_score>=.55 else None) if continuity else None
                            if camera.epoch!=camera.delivered.guard_epoch or late_veto:
                                self.gate.active=True;engine.clear_absence();engine.log_frames=0;engine.log_frame_time=None
                                newer_frame_vetoes+=1;force_full_after_focus=True
                                self.event('frame_review_veto',reason='late_new_frame_or_epoch',evidence=late_veto)
                                continue
                            ownership=self.validate_observation_owner(token,info,engine,'death')
                            if ownership=='stopped':break
                            if ownership=='paused':paused=True;continue
                            dry=self.runtime_dry or not self.exit_enabled
                            exit_watch=None
                            if dry:
                                if self.network:self.network.death_detected(False,monitor_token=token)
                                if settlement_confirmation:self.event('settlement_visual_confirmed',**settlement_confirmation)
                                message=('仅联网控制：已判定死亡，请手动退出游戏' if not self.exit_enabled else '只记录：已检测到 UI 消失，未发送退出请求')
                            else:
                                if not foreground_owner(info):
                                    self.validate_observation_owner(token,info,engine,'death',force_pause=True)
                                    paused=True;continue
                                if self.network:self.network.death_detected(True,monitor_token=token)
                                decision_clock=time.perf_counter();decision_wall=time.time_ns()
                                direct=exit_target.terminate() if exit_target else dict(accepted=False,state='unbound')
                                if settlement_confirmation and settlement_hint and direct.get('request_monotonic_ns'):
                                    settlement_confirmation['read_to_exit_ms']=round((direct['request_monotonic_ns']/1e9-settlement_hint['seen_monotonic'])*1000,3)
                                self.event('death_decision',capture_monotonic=now,decision_monotonic=decision_clock,
                                    decision_wall_ns=decision_wall,analysis_ms=(decision_clock-begin)*1000,features=dict(self.detector.last_features),
                                    diagnostic_episode=candidate_result.get('diagnostic_episode'),
                                    first_review_to_decision_ms=round((decision_clock-candidate_result['diagnostic_episode']['first_reviewed_monotonic'])*1000,3) if candidate_result.get('diagnostic_episode') else None)
                                self.event('process_termination_request',**direct)
                                self.record_exit_timing(engine,direct,previous_missing)
                                if settlement_confirmation:self.event('settlement_visual_confirmed',**settlement_confirmation)
                                if direct.get('accepted') or direct.get('state')=='already_exited':
                                    message='已请求结束游戏进程；退出确认中'
                                    exit_watch=(exit_target.detach(),info[1],info[0],direct.get('request_monotonic_ns'))
                                else:
                                    if not foreground_owner(info):
                                        self.validate_observation_owner(token,info,engine,'death',force_pause=True)
                                        paused=True;continue
                                    delivery={};message=send_exit_dual(info,delivery)
                                    self.event('input_delivery',pid=info[1],hwnd=info[0],result=message,**delivery)
                                    if delivery.get('any_request_accepted',False):
                                        process_handle=exit_target.detach() if exit_target and exit_target.handle else K.OpenProcess(0x100000,False,info[1])
                                        exit_watch=(process_handle,info[1],info[0],time.perf_counter_ns())
                                    elif target()!=info:
                                        self.validate_observation_owner(token,info,engine,'death',force_pause=True)
                                        paused=True;continue
                                    self.event('direct_exit_fallback',reason=direct.get('state'),result=message)
                            # Only a current dry-run decision or an attempted request
                            # consumes this session. Losing focus above never does.
                            if getattr(self,'keyboard_diagnostics',None):
                                self.keyboard_diagnostics.trigger(info[1],dict(candidate=input_snapshot('candidate'),dry_run=dry,combat_score=combat,delay_ms=self.gate.delay_ms))
                            self.active=False;self.token+=1
                            if exit_watch:
                                handle,pid,hwnd,requested=exit_watch
                                exit_watch=(handle,pid,self.token,hwnd,requested)
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
                            frame=camera.grab(region=(0,0,info[2],info[3]) if FENCE else region,copy=False)
                            if frame is None:continue
                            if target()!=info:break
                            self.detector.analyze(frame,full_size=(info[2],info[3]))
                            collect('after_candidate',time.perf_counter())
                        break
                    if now-window_start>=5:performance()
            except Exception as exc:
                self.cancel('采集/检测错误：'+str(exc))
            finally:
                if parallel_check:parallel_check.close()
                if continuity:
                    continuity.close();self.event('hud_continuity_audit',checked_frames=continuity.checked,errors=continuity.errors)
                if getattr(self,'hud_continuity',None) is continuity:self.hud_continuity=None
                if esc_audit:esc_audit.close()
                if settlement_watch:settlement_watch.close()
                if parallel_target:parallel_target.close()
                if exit_target:exit_target.close()
                if diagnostic:diagnostic.reset()
                performance()
                if camera:
                    try:camera.release()
                    except Exception:pass
                    self.event('capture_released')
                if getattr(self,'capture_session',None) is camera:self.capture_session=None
                if getattr(self,'parallel_session',None) is parallel_check:self.parallel_session=None

    def observe_exit(self,handle,pid,token,hwnd=0,requested_ns=None):
        began=time.perf_counter();origin=(requested_ns/1e9) if requested_ns else began
        window_gone=False;window_hidden=False;pending_reported=False
        try:
            result=0xFFFFFFFF
            while handle:
                result=K.WaitForSingleObject(handle,50)
                current=time.perf_counter();elapsed=current-origin
                owner=W.DWORD()
                if hwnd and not window_gone:
                    exists=bool(U.IsWindow(hwnd))
                    if exists:U.GetWindowThreadProcessId(hwnd,C.byref(owner))
                    if exists and owner.value==pid and not window_hidden and not U.IsWindowVisible(hwnd):
                        window_hidden=True;self.event('game_window_hidden',pid=pid,hwnd=hwnd,elapsed_ms=round(elapsed*1000,1))
                    if not exists or owner.value!=pid:
                        window_gone=True;self.event('game_window_gone',pid=pid,hwnd=hwnd,elapsed_ms=round(elapsed*1000,1))
                if result!=258 or current-began>=30:break
                if elapsed>=3 and not pending_reported:
                    pending_reported=True
                    self.event('exit_observation',pid=pid,state='pending',wait_result=258,elapsed_ms=round(elapsed*1000,1))
                    self.ui.put(('exit_check',(token,'退出确认中 · 3 秒后进程仍在运行，继续观察至 30 秒')))
            state='exited' if result==0 else ('still_running' if result==258 else 'unavailable')
            if state=='exited' and self.network:self.network.exited(pid=pid)
            self.event('exit_observation',pid=pid,state=state,wait_result=int(result),window_gone=window_gone,
                       elapsed_ms=round((time.perf_counter()-origin)*1000,1))
            message={'exited':'已确认游戏进程退出 · 回档结果需自行确认',
                'still_running':'退出未确认 · 请求后 30 秒游戏进程仍在运行',
                'unavailable':'无法确认游戏退出 · 进程观察不可用'}[state]
            self.ui.put(('exit_check',(token,message)))
        finally:
            if handle:K.CloseHandle(handle)

    def tick(self):
        try:
            while True:
                kind,text=self.ui.get_nowait()
                if kind=='network_status':
                    payload=text if isinstance(text,dict) else dict(text=text,revision=self.network_request_revision)
                    with self.lock:
                        if payload.get('revision')!=self.network_request_revision:continue
                        if payload.get('phase')=='stopping' and self.network and not self.network.enabled and not self.network.wanted:continue
                        text=payload['text']
                        if payload.get('enabled') is False:
                            self.network_session_started=False
                            if self.network_allowed and text.startswith('联网控制已停止') and '失败' not in text and payload.get('restore_confirmed'):
                                text='联网控制待命 · 已恢复联网，按 '+self.key_name+' 重新开启'
                        self.network_status.set(text)
                    continue
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
                if kind=='transfer_status':
                    self.transfer_feedback(text);continue
                if kind=='exit_check':
                    token,message=text
                    if token==self.token and not self.active and not self.arming:
                        self.status.set(message);self.state_head.set('本轮停止 · '+('游戏已退出' if message.startswith('已确认') else '请检查退出结果'))
                        self.bottom_status.set(message)
                    continue
                if kind=='diagnostic_saved':
                    self.diagnostic_status.set('诊断已保存：'+text);continue
                if kind=='overlay_waiting':
                    self.status.set(text);self.state_head.set('已暂停 · 等待确认游戏界面')
                    self.bottom_status.set('保持暂停 · F10可停止监控')
                    continue # Same pause, no second sound.
                (self.status if kind=='status' else self.scores).set(text)
                if kind=='status':
                    if text.startswith('监控中'):head='已暂停 · 等待回到游戏' if '暂停' in text else f'守护中 · {self.key_name} 可停止'
                    elif text.startswith('正在检查'):head='正在连接战局…'
                    elif '只记录：已检测到' in text:head='候选已记录 · 本轮停止'
                    elif '已发送一次 Alt+F4' in text or '已请求结束游戏进程' in text:head='已发送退出 · 本轮停止'
                    elif '未开启' in text or '失败' in text or '错误' in text:head='未连接 · 请检查提示'
                    else:head='待命 · 新的一轮'
                    self.state_head.set(head)
                    if text.startswith('监控中'):bottom='已暂停 · 切回游戏后继续' if '暂停' in text else f'已开启 · {self.key_name} 可关闭'
                    elif text.startswith('正在检查'):bottom='正在检查界面'
                    elif '已发送一次 Alt+F4' in text or '已请求结束游戏进程' in text:bottom='退出请求已发送 · 正在确认游戏退出'
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

    def transfer_feedback(self,payload):
        if payload.get('token')!=self.token or not self.active:return
        phase=payload.get('phase','idle');resumed=payload.get('resumed',False)
        captions={'confirm':'转移中 · 确认倒计时，死亡检测继续', 'transfer_menu':'转移中 · 已识别传递物品界面，死亡检测继续',
                  'countdown':'转移中 · 等待转移倒计时与加载，保持监控',
                  'return_unobserved':'转移中 · 未观察到过渡，HUD出现后请停止并重新开启',
                  'leaving_old_map':'转移中 · 等待旧HUD消失，加载保护中',
                  'loading':'转移中 · 等待加载后的战斗HUD',
                  'reacquiring_hud':'转移中 · 正在确认战斗HUD恢复'}
        caption=('监控中 · 已确认加载后的战斗HUD，自动恢复死亡检测' if resumed else
                 '监控中 · 转移标志消失，已回到战斗画面' if phase=='idle' else captions.get(phase,'转移中 · 等待战斗HUD'))
        if payload.get('paused'):caption='转移中 · 已切出游戏，切回后继续等待战斗HUD'
        if payload.get('stalled'):caption='转移中 · 暂无新画面，等待采集恢复'
        if payload.get('manual'):caption='转移中 · 切屏期间未观察到完整加载，请按快捷键停止后重新开启'
        if payload.get('long_wait'):caption='转移中 · 等待较久；不会自动大退，可按快捷键手动停止'
        if payload.get('hint_cancelled'):caption='监控中 · 已清除未确认的转移提示'
        heading=('已暂停 · 等待回到游戏' if payload.get('paused') else
                 '监控已恢复' if phase=='idle' else
                 '转移提示待确认' if phase in ('confirm','transfer_menu') else '转移等待中')
        self.status.set(caption);self.bottom_status.set(caption);self.state_head.set(heading)
        if resumed:
            played=False;error=None
            try:
                if '--smoke-test' not in sys.argv and not self.sounds.muted and self.sounds.enabled.get('enabled',False):played=self.sounds.play('enabled')
            except (RuntimeError,OSError) as exc:error=str(exc)
            self.event('transfer_resume_feedback',played=played,error=error)

    def close(self):
        self.cancel('程序退出');self.closed=True
        # Stop the producer independently of an in-flight slow image analysis.
        if getattr(self,'parallel_session',None):self.parallel_session.close()
        if getattr(self,'capture_session',None):
            try:self.capture_session.release()
            except Exception:pass
        if self.network:self.network.close()
        self.sounds.stop()
        self.hotkey_service.close()
        if getattr(self,'keyboard_diagnostics',None):self.keyboard_diagnostics.close()
        self.notice.close()
        self.root.destroy()
        self.window_icons.close()
        if getattr(self,'jsonl_log',None):
            try:self.jsonl_log.close()
            except Exception:pass

if __name__=='__main__':
    if '--review-check' in sys.argv:
        # Packaging-only synthetic check. No app, hotkeys, capture or networking.
        destination=Path(sys.argv[sys.argv.index('--review-check')+1]);result=dict(build=BUILD,ok=False)
        try:
            image=np.full((540,960,3),40,np.uint8);detector=Detector()
            detector.analyze(image,(960,540))
            title=EscTitleDetector();layout=dict(left=0,right=960,top=0,width=960,height=540)
            ticket=TitleTicket(title,image,(960,540),layout);value,elapsed=ticket.finish()
            result.update(ok=bool(detector.last_parallel.get('used') and not value['present'] and
                ticket.matches(layout,image) and not ticket.matches(layout,image.copy())),parallel_ui=detector.last_parallel,title_ms=elapsed)
        except Exception as exc:result['error']=repr(exc)
        destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        sys.exit(0 if result['ok'] else 2)
    if '--capture-check' in sys.argv:
        # Local packaging acceptance only: capture without starting the app,
        # loading preferences, registering hotkeys, or touching game/network state.
        result_path=Path(sys.argv[sys.argv.index('--capture-check')+1])
        check=None;result=dict(build=BUILD,ok=False,frames=[])
        try:
            width=U.GetSystemMetrics(0);height=U.GetSystemMetrics(1)
            region=(0,0,width,min(height,480))
            check=SharedCapture(AutoCapture(),30,region,lambda:True)
            deadline=time.perf_counter()+5
            while time.perf_counter()<deadline and len(result['frames'])<3:
                image=check.grab(region=region)
                if image is not None:
                    f=check.delivered
                    result['frames'].append(dict(sequence=f.sequence,source_time=f.captured_at,
                        source=f.provenance,shape=list(image.shape),capture_ms=f.capture_ms))
            result['ok']=len(result['frames'])>=2 and all(
                a['source_time']<b['source_time'] for a,b in zip(result['frames'],result['frames'][1:]))
        except Exception as exc:
            result['error']=repr(exc);result['cause']=repr(exc.__cause__)
        finally:
            if check:check.release()
            result_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        sys.exit(0 if result['ok'] else 2)
    mutex=K.CreateMutexW(None,False,'Local\\TarkovHUDMonitor_SingleInstance')
    if K.GetLastError()==183:
        U.MessageBoxW(None,'工具已经运行，请勿重复启动。','HUD 监控',0)
        sys.exit(0)
    try:
        app=App()
        if '--smoke-test' in sys.argv:
            import numpy as np
            title_probe=EscTitleDetector().detect(np.zeros((540,960,3),dtype=np.uint8),(960,1080))
            assert not title_probe['present'], 'Blank scene must not be an ESC title'
            transfer_probe=TransferDetector().detect(None,np.zeros((240,960,3),dtype=np.uint8),(960,1080))
            assert not transfer_probe['menu'] and not transfer_probe['countdown'], 'Blank scene must not be a transfer'
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
