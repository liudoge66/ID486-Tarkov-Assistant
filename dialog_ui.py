"""Use the main panel's bundled font and center dialogs on their owner window."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
from tkinter import font as tkfont

_registered=set()

def register_panel_font():
    path=Path(__file__).parent/'assets'/'fonts'/'Plix-Regular.otf'
    if str(path) not in _registered:
        gdi=C.WinDLL('gdi32',use_last_error=True)
        gdi.AddFontResourceExW.argtypes=[W.LPCWSTR,W.DWORD,C.c_void_p]
        gdi.AddFontResourceExW.restype=C.c_int
        if not gdi.AddFontResourceExW(str(path),0x10,None):
            raise OSError(C.get_last_error(),'无法加载主界面字体')
        _registered.add(str(path))

def panel_font(root, scale):
    register_panel_font()
    families=set(tkfont.families(root))
    family=next((name for name in ('普力士','Plix') if name in families),None)
    if family is None:raise RuntimeError('主界面字体未被Windows识别')
    # Negative Tk sizes are pixels, keeping text aligned with the image-based panel.
    return tkfont.Font(root=root,family=family,size=-max(14,min(22,round(24*scale))))

def center_dialog(window, parent):
    parent.update_idletasks();window.update_idletasks()
    width=window.winfo_reqwidth();height=window.winfo_reqheight()
    x=parent.winfo_rootx()+(parent.winfo_width()-width)//2
    y=parent.winfo_rooty()+(parent.winfo_height()-height)//2
    window.geometry(f'{width}x{height}{x:+d}{y:+d}')
    window.deiconify();window.update_idletasks()
    # Account for native title bars/borders so the visible outer rectangles center exactly.
    user=C.WinDLL('user32',use_last_error=True)
    user.GetAncestor.argtypes=[W.HWND,W.UINT];user.GetAncestor.restype=W.HWND
    user.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)];user.GetWindowRect.restype=W.BOOL
    user.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
    owner=user.GetAncestor(parent.winfo_id(),2);hwnd=user.GetAncestor(window.winfo_id(),2)
    outer=W.RECT();dialog=W.RECT()
    if user.GetWindowRect(owner,C.byref(outer)) and user.GetWindowRect(hwnd,C.byref(dialog)):
        x=(outer.left+outer.right-(dialog.right-dialog.left))//2
        y=(outer.top+outer.bottom-(dialog.bottom-dialog.top))//2
        user.SetWindowPos(hwnd,None,x,y,0,0,0x15)
    window.lift()
