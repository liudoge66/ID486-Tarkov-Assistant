"""Thread-owned global hotkeys with transactional rebind and OS conflict probes."""
import ctypes as C
from ctypes import wintypes as W
import queue
import threading

U=C.WinDLL('user32',use_last_error=True);K=C.WinDLL('kernel32',use_last_error=True)
U.RegisterHotKey.argtypes=[W.HWND,C.c_int,W.UINT,W.UINT]
U.UnregisterHotKey.argtypes=[W.HWND,C.c_int]
U.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
U.GetMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT];U.GetMessageW.restype=W.BOOL
U.PostThreadMessageW.argtypes=[W.DWORD,W.UINT,W.WPARAM,W.LPARAM]
PRESETS=[(0,0x79),(0,0x24),(0,0x78),(0,0x77),(3,0x79)]

def label(combo):
    mods,vk=combo
    names={0x24:'Home',0x23:'End',0x2D:'Insert',0x2E:'Delete',0x13:'Pause',0x91:'ScrollLock',0x21:'PageUp',0x22:'PageDown'}
    key=('F'+str(vk-0x6F)) if 0x70<=vk<=0x87 else names.get(vk,chr(vk) if 0x30<=vk<=0x5A else 'VK'+str(vk))
    return '+'.join([s for bit,s in ((2,'Ctrl'),(1,'Alt'),(4,'Shift')) if mods&bit]+[key])

def validate(combo):
    if not isinstance(combo,(tuple,list)) or len(combo)!=2:raise ValueError('无效快捷键')
    mods,vk=combo
    if type(mods) is not int or type(vk) is not int or mods&~7:raise ValueError('支持 Ctrl / Alt / Shift 组合')
    allowed=set(range(0x70,0x88))|{0x24,0x23,0x2D,0x2E,0x13,0x91,0x21,0x22}
    if vk not in allowed and not (mods and (0x30<=vk<=0x5A)):raise ValueError('单键请选择功能键或导航键；字母/数字需搭配 Ctrl、Alt 或 Shift')
    if combo in ((1,0x73),(3,0x2E)) or vk==0x7B:raise ValueError('Alt+F4、Ctrl+Alt+Delete 和 F12 保留给系统/截图')
    return (mods,vk)

class Hotkeys:
    def __init__(self,events,combo=(0,0x79)):
        self.events=events;self.combo=validate(combo);self.key_name=label(self.combo)
        self.registered=[];self.active_id=None;self.commands=queue.Queue()
        self.ready=threading.Event();self.thread_id=None
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
        if not self.ready.wait(3):raise RuntimeError('快捷键线程启动超时')

    def run(self):
        self.thread_id=K.GetCurrentThreadId();msg=W.MSG();U.PeekMessageW(C.byref(msg),None,0,0,0)
        try:
            if U.RegisterHotKey(None,1,0x4000|self.combo[0],self.combo[1]):self.active_id=1;self.registered[:]=[1]
            self.ready.set()
            while U.GetMessageW(C.byref(msg),None,0,0)>0:
                if msg.message==0x312 and msg.wParam==self.active_id:self.events.put(('hotkey',self.key_name))
                elif msg.message==0x8001:
                    while True:
                        try:action,combo,done,out=self.commands.get_nowait()
                        except queue.Empty:break
                        if action=='clear':
                            if self.active_id is not None:U.UnregisterHotKey(None,self.active_id)
                            self.active_id=None;self.registered.clear();self.key_name='未设置';out.append(True)
                        elif combo==self.combo and self.active_id is not None:out.append(True)
                        else:
                            ident=3 if self.active_id==1 else 1
                            ok=bool(U.RegisterHotKey(None,ident,0x4000|combo[0],combo[1]))
                            if ok:
                                if action=='set':
                                    if self.active_id is not None:U.UnregisterHotKey(None,self.active_id)
                                    self.active_id=ident;self.registered[:]=[ident];self.combo=combo;self.key_name=label(combo)
                                else:U.UnregisterHotKey(None,ident)
                            out.append(ok)
                        done.set()
        finally:
            if self.active_id is not None:U.UnregisterHotKey(None,self.active_id)

    def request(self,action,combo):
        combo=validate(combo);done=threading.Event();out=[]
        self.commands.put((action,combo,done,out));U.PostThreadMessageW(self.thread_id,0x8001,0,0)
        if not done.wait(2):raise RuntimeError('快捷键线程没有响应')
        return out[0]

    def available(self,combo):return self.request('probe',combo)
    def rebind(self,combo):return self.request('set',combo)
    def clear(self):return self.request('clear',self.combo)
    def close(self):
        if self.thread_id:U.PostThreadMessageW(self.thread_id,0x12,0,0)
        self.thread.join(timeout=1)
