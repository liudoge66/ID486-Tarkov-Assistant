"""Isolated video playback/recognition test. Only its own window may receive input."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import os,time,json,csv,tkinter as tk
from tkinter import filedialog
import cv2
from PIL import Image,ImageTk
from detector import Detector,Gate,ExtractionGuard,ArmConfirmation
from video_reader import VideoReader

def run_video_test(path=None,result_path=None):
    root=tk.Tk();root.title('ID486 · 临时录像测试播放器');root.configure(bg='#18181c')
    if not path:path=filedialog.askopenfilename(parent=root,title='选择录像进行测试',filetypes=[('录像','*.mp4 *.mkv *.mov *.avi')])
    if not path:root.destroy();return 2
    source=Path(path)
    try:cap=VideoReader(source)
    except Exception as exc:
        from tkinter import messagebox
        messagebox.showerror("录像无法读取",str(exc),parent=root);root.destroy();return 3
    if not cap.isOpened():root.destroy();return 3
    fps=cap.get(cv2.CAP_PROP_FPS);total=cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if fps<=0:cap.release();root.destroy();return 3
    import monitor as m
    base=Path(os.sys.executable).parent if getattr(os.sys,'frozen',False) else Path(__file__).parent
    folder=base/'video_test_results';folder.mkdir(exist_ok=True)
    prefix=Path(result_path).stem if result_path else source.stem+'_'+str(time.time_ns());report=dict(video=source.name,source_fps=fps,frame_count=total,source_width=cap.get(cv2.CAP_PROP_FRAME_WIDTH),source_height=cap.get(cv2.CAP_PROP_FRAME_HEIGHT),mode='decoded video UI + own player Alt+F4 + WM_CLOSE',delay_ms=300,sample_hz=30,events=[])
    rows=[];detector=Detector();gate=Gate(delay_ms=300);guard=ExtractionGuard();arm=ArmConfirmation(30)
    label=tk.Label(root,bg='black');label.pack();note=tk.StringVar(value='录像测试 · 自动等待稳定战斗HUD · 只会关闭本播放器')
    tk.Label(root,textvariable=note,bg='#18181c',fg='white',pady=12).pack()
    screen_w=root.winfo_screenwidth();aspect=cap.get(cv2.CAP_PROP_FRAME_WIDTH)/cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    display_w=min(1000,screen_w-100,round((root.winfo_screenheight()-160)*aspect));display_h=round(display_w/aspect)
    root.geometry(f'{display_w}x{display_h+85}');label.configure(width=display_w,height=display_h)
    state=dict(fired=False,sent=False,done=False,extracted=False,frame_index=-1,next_check=0.,next_draw=0.,exit_code=2)
    user=C.WinDLL('user32',use_last_error=True);user.GetAncestor.argtypes=[W.HWND,W.UINT];user.GetAncestor.restype=W.HWND
    user.GetForegroundWindow.restype=W.HWND;user.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
    user.SetForegroundWindow.argtypes=[W.HWND];user.SetForegroundWindow.restype=W.BOOL
    root.update_idletasks();hwnd=int(user.GetAncestor(root.winfo_id(),2));pid=os.getpid()
    def own_target():
        current=user.GetForegroundWindow();p=W.DWORD();user.GetWindowThreadProcessId(current,C.byref(p))
        return (hwnd,pid,display_w,display_h) if int(current or 0)==hwnd and p.value==pid else None
    def event(kind,**data):report['events'].append(dict(kind=kind,wall_ns=time.time_ns(),monotonic=time.perf_counter(),**data))
    def save(outcome):
        report['outcome']=outcome;report['player_pid']=pid
        destination=Path(result_path) if result_path else folder/(prefix+'.json')
        destination.parent.mkdir(parents=True,exist_ok=True)
        temp=destination.with_suffix('.json.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(destination)
        if rows:
            with (folder/(prefix+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    def close():
        if state['done']:return
        state['done']=True;cap.release()
        outcome='player_closed_after_dual_request' if state['sent'] else 'closed_by_user'
        state['exit_code']=0 if state['sent'] else 2;event('window_close_received');save(outcome);root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    def finish(outcome):
        if state['done']:return
        state['done']=True;state['exit_code']=2;event('test_finished',reason=outcome);cap.release();save(outcome);root.destroy()
    began=time.perf_counter()+1.5
    def tick():
        if state['done']:return
        now=time.perf_counter();elapsed=max(0.,now-began)
        if state['fired']:
            if now-state['send_time']>3:finish('input_sent_but_player_not_closed' if state['sent'] else 'input_not_sent')
            else:root.after(15,tick)
            return
        wanted=int(elapsed*fps);frame=None
        # Playback uses wall time; decode intermediate frames without expensive rendering.
        while state['frame_index']<wanted:
            try:ok,frame=cap.read()
            except Exception as exc:event('decode_error',error=repr(exc));finish('decode_error');return
            state['frame_index']+=1
            if not ok:finish('extraction_protected' if state['extracted'] else 'no_trigger_before_end');return
        if frame is not None:
            t=cap.last_time
            if t+1e-6>=state['next_check']:
                state['next_check']=t+1/30;scores,menu=detector.analyze(frame);features=detector.last_features
                if not gate.active and not state['extracted'] and arm.update(features,t):
                    if gate.arm(scores,menu_present=menu>=.55,combat_score=features['combat_score'],features=features):event('armed',video_seconds=t,evidence=arm.summary())
                extracted=guard.update(t,features['combat_score'],menu>=.55,detector.last_extraction) if gate.active else False
                if extracted:state['extracted']=True;gate.cancel();event('extraction',video_seconds=t)
                fired=gate.update(scores,menu_present=menu>=.55,now=t,combat_score=features['combat_score'])
                rows.append(dict(video_seconds=t,combat=features['combat_score'],body=features['body_shape'],labels=features['label_shape'],menu=menu,layout=json.dumps(detector.layout_info),active=gate.active,trigger=fired,extraction=extracted))
                if extracted:finish('extraction_protected');return
                note.set(f'录像测试 {t:.2f}s · HUD {features["combat_score"]:.0%} · 菜单 {menu:.0%} · '+('监测中' if gate.active else '等待稳定HUD' if not state['extracted'] else '撤离保护已停止'))
                if fired:
                    state['fired']=True;event('candidate',video_seconds=t);delivery={}
                    message=m.send_exit_dual((hwnd,pid,display_w,display_h),delivery,target_check=own_target)
                    state['sent']=delivery.get('any_request_accepted',False);state['send_time']=time.perf_counter();event('input_delivery',result=message,details=delivery)
            if now>=state['next_draw']:
                state['next_draw']=now+1/30
                rgb=cv2.cvtColor(cv2.resize(frame,(display_w,display_h)),cv2.COLOR_BGR2RGB)
                label.image=ImageTk.PhotoImage(Image.fromarray(rgb));label.configure(image=label.image)
        root.after(5,tick)
    root.after(100,lambda:user.SetForegroundWindow(hwnd));root.after(150,tick);root.mainloop()
    return state['exit_code']
