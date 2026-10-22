"""Temporary opt-in-by-build game keyboard diagnostics. Never records text/clipboard."""
import ctypes as C
from ctypes import wintypes as W
from collections import deque,Counter
from pathlib import Path
import json,threading,time

class EventWindow:
    def __init__(self,seconds=15,limit=20000):
        self.seconds=seconds;self.limit=limit;self.ring=deque();self.pending=[];self.dropped=0;self.armed=False;self.pid=None;self.lock=threading.RLock()
    def prune(self,now):
        while self.ring and self.ring[0]['t']<now-self.seconds:self.ring.popleft()
    def start(self,pid):
        with self.lock:self.ring.clear();self.pid=pid;self.armed=True
    def pause(self):
        with self.lock:self.armed=False;self.ring.clear()
    def add(self,event,pid):
        with self.lock:
            t=event['t'];self.prune(t)
            if self.armed and pid==self.pid:
                if len(self.ring)>=self.limit:self.ring.popleft();self.dropped+=1
                self.ring.append(dict(event))
            for p in self.pending:
                if p['pid']==pid and p['trigger_t']<=t<=p['end']:
                    if len(p['events'])<self.limit*2:p['events'].append(dict(event))
                    else:p['dropped']+=1
    def trigger(self,now,pid,metadata):
        with self.lock:
            self.prune(now)
            if len(self.pending)>=4:return False
            self.pending.append(dict(trigger_t=now,trigger_wall_ns=time.time_ns(),end=now+self.seconds,pid=pid,metadata=dict(metadata),events=[dict(e) for e in self.ring if now-self.seconds<=e['t']<=now],dropped=self.dropped))
            self.armed=False;self.ring.clear();self.dropped=0
            return True
    def ready(self,now,force=False):
        with self.lock:
            self.prune(now);out=[p for p in self.pending if force or now>=p['end']]
            self.pending=[p for p in self.pending if p not in out]
            for p in out:p['complete']=now>=p['end'];p['actual_end']=min(now,p['end'])
            return out

class KeyboardDiagnostics:
    def __init__(self,folder):
        self.window=EventWindow();self.folder=Path(folder);self.stop=threading.Event();self.ready=threading.Event();self.available=False;self.error=None
        self.max_callback_ms=0.;self.callback_count=0;self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start();self.ready.wait(2)
    def start(self,pid):self.window.start(pid)
    def pause(self):self.window.pause()
    def trigger(self,pid,metadata):return self.window.trigger(time.perf_counter(),pid,metadata)
    def run(self):
        u=C.WinDLL('user32',use_last_error=True);k=C.WinDLL('kernel32',use_last_error=True)
        PTR=C.c_ssize_t
        class Key(C.Structure):_fields_=[('vk',W.DWORD),('scan',W.DWORD),('flags',W.DWORD),('stamp',W.DWORD),('extra',C.c_size_t)]
        PROC=C.WINFUNCTYPE(PTR,C.c_int,W.WPARAM,W.LPARAM)
        u.SetWindowsHookExW.argtypes=[C.c_int,PROC,W.HINSTANCE,W.DWORD];u.SetWindowsHookExW.restype=W.HANDLE
        u.CallNextHookEx.argtypes=[W.HANDLE,C.c_int,W.WPARAM,W.LPARAM];u.CallNextHookEx.restype=PTR
        u.GetForegroundWindow.restype=W.HWND
        u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
        u.UnhookWindowsHookEx.argtypes=[W.HANDLE]
        u.CallNextHookEx.restype=PTR
        u.SetTimer.argtypes=[W.HWND,C.c_size_t,W.UINT,C.c_void_p];u.SetTimer.restype=C.c_size_t
        u.GetMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT]
        self.tid=k.GetCurrentThreadId()
        @PROC
        def callback(code,msg,ptr):
            begin=time.perf_counter()
            try:
                if code>=0 and (self.window.armed or self.window.pending):
                    pid=W.DWORD();hwnd=u.GetForegroundWindow();u.GetWindowThreadProcessId(hwnd,C.byref(pid))
                    key=C.cast(ptr,C.POINTER(Key)).contents
                    names={0xA0:'LShift',0xA1:'RShift',0xA2:'LCtrl',0xA3:'RCtrl',0xA4:'LAlt',0xA5:'RAlt',0x5B:'LWin',0x5C:'RWin',0x73:'F4'}
                    name=names.get(key.vk,('F'+str(key.vk-0x6F)) if 0x70<=key.vk<=0x87 else ('VK_%02X'%key.vk))
                    self.window.add(dict(t=begin,wall_ns=time.time_ns(),vk=int(key.vk),key=name,scan=int(key.scan),edge='up' if key.flags&0x80 else 'down',injected=bool(key.flags&0x10),lower_integrity_injected=bool(key.flags&2),os_time_ms=int(key.stamp),hwnd=int(hwnd or 0),pid=int(pid.value)),int(pid.value))
                    self.callback_count+=1;self.max_callback_ms=max(self.max_callback_ms,(time.perf_counter()-begin)*1000)
            except Exception:pass
            return u.CallNextHookEx(None,code,msg,ptr)
        hook=u.SetWindowsHookExW(13,callback,None,0)
        if not hook:self.error='keyboard hook failed '+str(C.get_last_error());self.ready.set();return
        self.available=True;timer=u.SetTimer(None,0,100,None);self.ready.set();message=W.MSG()
        try:
            while not self.stop.is_set() and u.GetMessageW(C.byref(message),None,0,0)>0:
                u.TranslateMessage(C.byref(message));u.DispatchMessageW(C.byref(message))
                for item in self.window.ready(time.perf_counter()):threading.Thread(target=self.save,args=(item,),daemon=True).start()
        finally:
            u.KillTimer(None,timer);u.UnhookWindowsHookEx(hook)
            for item in self.window.ready(time.perf_counter(),force=True):self.save(item)
    def save(self,item):
        try:
            self.folder.mkdir(parents=True,exist_ok=True)
            counts=Counter();held=set()
            for e in item['events']:
                e['relative_ms']=round((e['t']-item['trigger_t'])*1000,3)
                pair=(e['vk'],e['injected']);repeat=e['edge']=='down' and pair in held;e['repeat']=repeat
                counts[e['key']+':'+e['edge']+(':injected' if e['injected'] else ':hardware')]+=1
                if repeat:counts[e['key']+':repeat']+=1
                if e['edge']=='down':held.add(pair)
                else:held.discard(pair)
            item['summary']=dict(counts);item['scope']='only foreground target PID; no text translation; prior 15s + following 15s'
            item['repeat_note']='repeat inferred within retained window; state before window may be unknown'
            item['hook_metrics']=dict(callback_count=self.callback_count,max_callback_ms=self.max_callback_ms)
            name='keyboard_'+str(time.time_ns())+'.json';tmp=self.folder/(name+'.tmp');tmp.write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.folder/name)
            files=sorted(self.folder.glob('keyboard_*.json'),key=lambda p:p.stat().st_mtime,reverse=True)
            for old in files[10:]:old.unlink(missing_ok=True)
        except OSError as exc:self.error=str(exc)
    def close(self):
        self.stop.set()
        if getattr(self,'tid',None):C.WinDLL('user32').PostThreadMessageW(self.tid,0x12,0,0)
        self.thread.join(timeout=3)
