"""Bind termination to the verified game process object, never a name-wide kill."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import time

K = C.WinDLL('kernel32', use_last_error=True)
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
K.QueryFullProcessImageNameW.restype = W.BOOL
K.GetProcessId.argtypes = [W.HANDLE]
K.GetProcessId.restype = W.DWORD
K.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
K.TerminateProcess.restype = W.BOOL
K.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
K.WaitForSingleObject.restype = W.DWORD
K.CloseHandle.argtypes = [W.HANDLE]
K.CloseHandle.restype = W.BOOL
K.GetProcessTimes.argtypes = [W.HANDLE]+[C.POINTER(W.FILETIME)]*4
K.GetProcessTimes.restype = W.BOOL


class GameProcess:
    def __init__(self, pid):
        self.pid = int(pid)
        self.handle = None
        self.can_terminate = False
        self.error = None
        self.creation_unix = None
        # Keep this handle throughout the armed session. A reused PID cannot
        # redirect a later termination request to another process object.
        for rights in (0x1000 | 0x100000 | 0x0001, 0x1000 | 0x100000):
            C.set_last_error(0)
            handle = K.OpenProcess(rights, False, self.pid)
            if not handle:
                self.error = C.get_last_error()
                continue
            name = C.create_unicode_buffer(32768)
            size = W.DWORD(len(name))
            valid = (K.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)) and
                     Path(name.value).name.lower() == 'escapefromtarkov.exe' and
                     K.GetProcessId(handle) == self.pid and K.WaitForSingleObject(handle, 0) == 258)
            if not valid:
                K.CloseHandle(handle)
                self.error = 'process_identity_unavailable'
                return
            self.handle = handle
            self.executable = Path(name.value)
            created,ended,kernel,user=(W.FILETIME() for _ in range(4))
            if K.GetProcessTimes(handle,C.byref(created),C.byref(ended),C.byref(kernel),C.byref(user)):
                self.creation_unix=((created.dwHighDateTime<<32)|created.dwLowDateTime)/1e7-11644473600
            self.can_terminate = bool(rights & 1)
            if self.can_terminate:
                self.error = None
            return

    def terminate(self):
        report = dict(policy='terminate_verified_game', pid=self.pid,
                      request_wall_ns=time.time_ns(), request_monotonic_ns=time.perf_counter_ns(),
                      accepted=False, error=self.error)
        if not self.handle:
            report['state'] = 'unbound'
        elif K.WaitForSingleObject(self.handle, 0) == 0:
            report['state'] = 'already_exited'
        elif not self.can_terminate:
            report['state'] = 'terminate_access_unavailable'
        else:
            C.set_last_error(0)
            report['accepted'] = bool(K.TerminateProcess(self.handle, 1))
            report['error'] = C.get_last_error()
            report['state'] = 'requested' if report['accepted'] else 'request_failed'
        report['returned_monotonic_ns'] = time.perf_counter_ns()
        report['call_ms'] = (report['returned_monotonic_ns']-report['request_monotonic_ns'])/1e6
        return report

    def detach(self):
        handle, self.handle = self.handle, None
        return handle

    def close(self):
        if self.handle:
            K.CloseHandle(self.handle)
            self.handle = None
