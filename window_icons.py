"""Edition-specific Windows icons with exact DPI-sized small and large handles."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from edition import EDITION, FENCE

ICON_FILE='fence_hood.ico' if EDITION=='Fence' else 'pmc_character.ico'
APP_ID='Liudoge.ID486' if not EDITION else 'Liudoge.ID486.'+EDITION
U=C.WinDLL('user32',use_last_error=True)
U.GetAncestor.argtypes=[W.HWND,W.UINT];U.GetAncestor.restype=W.HWND
U.LoadImageW.argtypes=[W.HINSTANCE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT];U.LoadImageW.restype=W.HICON
U.DestroyIcon.argtypes=[W.HICON];U.DestroyIcon.restype=W.BOOL
U.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];U.SendMessageW.restype=C.c_ssize_t
U.GetSystemMetrics.argtypes=[C.c_int];U.GetSystemMetrics.restype=C.c_int
if hasattr(U,'GetDpiForWindow'):
    U.GetDpiForWindow.argtypes=[W.HWND];U.GetDpiForWindow.restype=W.UINT
if hasattr(U,'GetSystemMetricsForDpi'):
    U.GetSystemMetricsForDpi.argtypes=[C.c_int,W.UINT];U.GetSystemMetricsForDpi.restype=C.c_int

def icon_path():return Path(__file__).parent/'assets'/ICON_FILE

def set_app_identity():
    shell=C.WinDLL('shell32',use_last_error=True)
    fn=shell.SetCurrentProcessExplicitAppUserModelID;fn.argtypes=[W.LPCWSTR];fn.restype=C.c_long
    return fn(APP_ID)==0

def window_dpi(hwnd):return int(U.GetDpiForWindow(hwnd) or 96) if hasattr(U,'GetDpiForWindow') else 96

def metric(index,dpi):
    return int(U.GetSystemMetricsForDpi(index,dpi)) if hasattr(U,'GetSystemMetricsForDpi') else int(U.GetSystemMetrics(index))

def load_icon(width,height=None):
    handle=U.LoadImageW(None,str(icon_path()),1,width,height or width,0x10)
    if not handle:raise OSError(C.get_last_error(),'Unable to load edition icon')
    return handle

class WindowIcons:
    def __init__(self,root):
        self.root=root;self.handles=[];self.dpi=None;self.pending=None;self.closed=False
        root.update_idletasks();self.hwnd=U.GetAncestor(root.winfo_id(),2)
        self.refresh();root.bind('<Configure>',self.schedule,add='+')

    def schedule(self,event):
        if self.closed or event.widget is not self.root or self.pending is not None:return
        self.pending=self.root.after(80,self.refresh)

    def refresh(self):
        self.pending=None
        if self.closed:return
        hwnd=U.GetAncestor(self.root.winfo_id(),2)
        if hwnd!=self.hwnd:self.hwnd=hwnd;self.dpi=None
        dpi=window_dpi(self.hwnd)
        if dpi==self.dpi:return
        small=load_icon(metric(49,dpi),metric(50,dpi))
        try:large=load_icon(metric(11,dpi),metric(12,dpi))
        except Exception:U.DestroyIcon(small);raise
        previous=self.handles
        U.SendMessageW(self.hwnd,0x80,0,small) # WM_SETICON / ICON_SMALL: title bar
        U.SendMessageW(self.hwnd,0x80,1,large) # WM_SETICON / ICON_BIG: taskbar / Alt+Tab
        self.handles=[small,large];self.dpi=dpi
        for handle in previous:U.DestroyIcon(handle)

    def close(self):
        self.closed=True
        if self.pending is not None:
            try:self.root.after_cancel(self.pending)
            except Exception:pass
            self.pending=None
        for handle in self.handles:U.DestroyIcon(handle)
        self.handles=[]
