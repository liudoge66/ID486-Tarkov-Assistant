import sys,queue
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import monitor as m
calls=[];sleeps=[]
class FakeU:
    def GetAsyncKeyState(self,key):return 0
    def GetForegroundWindow(self):return 123
    def SendInput(self,count,seq,size):
        calls.append([(x.ki.wVk,x.ki.dwFlags) for x in seq]);return count
m.U=FakeU();m.target=lambda:(123,456,3840,2160);m.time.sleep=lambda t:sleeps.append(t)
r={};assert '已发送一次' in m.send_alt_f4((123,456,3840,2160),r)
assert calls==[[(18,0),(115,0)],[(115,2),(18,2)]]
assert sleeps==[.04] and r['down_count']==r['up_count']==2
calls.clear();assert '失去前台' in m.send_alt_f4((999,456,3840,2160),{});assert not calls
class Partial(FakeU):
    def SendInput(self,count,seq,size):
        super().SendInput(count,seq,size);return 1 if len(calls)==1 else 2
m.U=Partial();assert '未完整' in m.send_alt_f4(None,{});assert calls[-1]==[(115,2),(18,2)]
class FakeK:
    result=0
    def WaitForSingleObject(self,h,ms):assert ms in (3000,27000);return self.result
    def CloseHandle(self,h):pass
m.K=FakeK();app=m.App.__new__(m.App);app.ui=queue.Queue();events=[];app.event=lambda kind,**data:events.append((kind,data))
for result,state in [(0,'exited'),(258,'still_running'),(0xFFFFFFFF,'unavailable')]:
    m.K.result=result;app.observe_exit(123,456,7);assert events[-1][1]['state']==state
    assert app.ui.get()[0]=='exit_check'
    while not app.ui.empty():app.ui.get()
print('PASS: ordered 40ms chord, focus guard, partial-send release, exited/still-running/unavailable outcomes; no real input or game action.')

class Delayed(FakeK):
    def WaitForSingleObject(self,h,ms):return 258 if ms==3000 else 0
m.K=Delayed();app.observe_exit(123,456,7);assert events[-2][1]['state']=='pending' and events[-1][1]['state']=='exited'
print('PASS: delayed exit after first 3 seconds is observed, not misreported as final failure.')
