"""Read an atomic Windows snapshot without blocking its replacement by the broker."""
import ctypes as C
from ctypes import wintypes as W
import os,json

def read_snapshot(path):
    if os.name!='nt':return json.loads(path.read_text(encoding='utf-8-sig'))
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
    kernel.CreateFileW.restype=W.HANDLE
    kernel.GetFileSizeEx.argtypes=[W.HANDLE,C.POINTER(C.c_longlong)]
    kernel.ReadFile.argtypes=[W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.c_void_p]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
    if handle in (None,C.c_void_p(-1).value):raise C.WinError(C.get_last_error())
    try:
        size=C.c_longlong()
        if not kernel.GetFileSizeEx(handle,C.byref(size)):raise C.WinError(C.get_last_error())
        if not 0<size.value<=1048576:raise ValueError('Invalid broker snapshot size')
        buffer=C.create_string_buffer(size.value);count=W.DWORD()
        if not kernel.ReadFile(handle,buffer,size.value,C.byref(count),None):raise C.WinError(C.get_last_error())
        return json.loads(buffer.raw[:count.value].decode('utf-8-sig'))
    finally:kernel.CloseHandle(handle)
