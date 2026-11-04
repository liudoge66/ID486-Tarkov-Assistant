"""Bounded asynchronous ESC checks and exact model-input evidence."""
from pathlib import Path
from collections import deque
from datetime import datetime
import json,queue,threading,time,uuid
import cv2


class EscAudit:
    def __init__(self,root,event,images=False):
        self.root=Path(root);self.event=event;self.images=images
        self.session=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:6]
        self.queue=queue.Queue(maxsize=16);self.closed=threading.Event()
        self.last_key=None;self.last_time=-100.;self.last_image=-100.;self.dropped=0
        self.thread=threading.Thread(target=self._write,name='EscAuditWriter',daemon=True);self.thread.start()

    def submit(self,record,image=None,force=False):
        if self.closed.is_set():return False
        now=time.perf_counter();title=record.get('title') or {}
        key=(record.get('kind'),record.get('called'),record.get('reason'),title.get('present'),record.get('action'))
        changed=key!=self.last_key
        if not force and not changed and now-self.last_time<1.:return False
        packet=dict(record,audit_session=self.session,timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'))
        if record.get('frame_monotonic') is not None:packet['capture_to_audit_ms']=round((now-record['frame_monotonic'])*1000,3)
        if record.get('analysis_started') is not None:packet['analysis_to_audit_ms']=round((now-record['analysis_started'])*1000,3)
        # The model input is an owned, read-only resize; never retain capture buffers.
        picture=image if self.images and image is not None and (force or changed or now-self.last_image>=2.) else None
        try:self.queue.put_nowait((packet,picture))
        except queue.Full:self.dropped+=1;return False
        self.last_key=key;self.last_time=now
        if picture is not None:self.last_image=now
        return True

    def _write(self):
        retained=deque();index=0
        while not self.closed.is_set() or not self.queue.empty():
            try:record,image=self.queue.get(timeout=.1)
            except queue.Empty:continue
            try:
                self.root.mkdir(parents=True,exist_ok=True)
                if image is not None:
                    folder=self.root/self.session;folder.mkdir(exist_ok=True)
                    index+=1;path=folder/('esc_roi_'+str(index).zfill(4)+'.png')
                    ok,encoded=cv2.imencode('.png',image)
                    if ok:
                        path.write_bytes(encoded.tobytes());record['model_input_path']=str(path)
                        retained.append(path)
                        while len(retained)>6:retained.popleft().unlink(missing_ok=True)
                log=self.root/'esc_checks.jsonl'
                if log.exists() and log.stat().st_size>=8*1024*1024:log.replace(self.root/'esc_checks.previous.jsonl')
                with log.open('a',encoding='utf-8') as stream:stream.write(json.dumps(record,ensure_ascii=False,default=str)+'\n')
                title=record.get('title') or {}
                self.event('esc_title_audit',pid=record.get('pid'),state='已调用' if record.get('called') else '未调用',
                    reason=record.get('reason'),base_menu=record.get('base_menu'),
                    title_present=title.get('present'),title_boxes=len(title.get('boxes',[])),
                    capture_region=record.get('capture_region'),frame_size=record.get('frame_size'),
                    frame_sequence=record.get('frame_sequence'),action=record.get('action'),
                    path=record.get('model_input_path'),frame_wall_ns=record.get('frame_wall_ns'))
            except (OSError,ValueError,cv2.error) as exc:
                try:self.event('esc_title_audit_error',reason=type(exc).__name__)
                except (OSError,ValueError):pass # App journal may already be closed.

    def close(self):
        self.closed.set()
        if threading.current_thread() is not self.thread:self.thread.join(timeout=.8)


def title_skip_reason(expanded,combat,menu,transfer_protected,fast_candidate):
    if fast_candidate:return 'settlement_fast_candidate'
    if transfer_protected:return 'transfer_protected'
    if not expanded:return 'not_expanded'
    if combat>=.48:return 'combat_hud_clear'
    if menu>=.55:return 'legacy_menu_confirmed'
    return None
