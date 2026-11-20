"""command.json rename must survive a briefly held broker read handle (WinError 5) and give up only after its budget."""
import ctypes as C,ctypes.wintypes as W,os,sys,tempfile,threading,time,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from network_control import NetworkController

K=C.WinDLL('kernel32',use_last_error=True);K.CreateFileW.restype=W.HANDLE
K.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]


def hold(path):
    # Same sharing as the broker's ReadShared: read|write|delete.
    return K.CreateFileW(str(path),0x80000000,1|2|4,None,3,0,None)


class CommandRetryTests(unittest.TestCase):
    def setUp(self):
        self.dir=Path(tempfile.mkdtemp());self.target=self.dir/'command.json';self.tmp=self.dir/'command.tmp'
        self.target.write_text('old');self.tmp.write_text('new');self.events=[]
        self.owner=types.SimpleNamespace(event=lambda kind,**d:self.events.append((kind,d)))

    def replace(self,**kw):return NetworkController.replace_command(self.owner,self.tmp,self.target,'prepare',**kw)

    def test_free_file_no_retry_event(self):
        self.replace();self.assertEqual(self.target.read_text(),'new');self.assertEqual(self.events,[])

    def test_short_hold_is_retried(self):
        h=hold(self.target);self.assertNotEqual(h,W.HANDLE(-1).value)
        threading.Timer(.03,lambda:K.CloseHandle(h)).start()
        self.replace()
        self.assertEqual(self.target.read_text(),'new')
        kind,d=self.events[-1];self.assertEqual(kind,'network_command_retry');self.assertFalse(d['gave_up']);self.assertGreater(d['attempts'],0)

    def test_long_hold_raises_after_budget(self):
        h=hold(self.target)
        try:
            start=time.monotonic()
            with self.assertRaises(PermissionError):self.replace(budget=.1)
            self.assertGreaterEqual(time.monotonic()-start,.1)
        finally:K.CloseHandle(h)
        self.assertTrue(self.events[-1][1]['gave_up']);self.assertEqual(self.target.read_text(),'old')


if __name__=='__main__':unittest.main()
