"""Bounded, game-only top-strip evidence. No capture or input APIs here."""
from collections import deque
from pathlib import Path
from datetime import datetime,timezone
import json,queue,threading,time,re,shutil
import cv2

class Diagnostics:
    def __init__(self,root,event):
        self.root=Path(root);self.event=event
        self.ring=deque(maxlen=6);self.pending=None;self.count=0;self.last_mark=-100.;self.last_frame=-100.
        self.jobs=queue.Queue(maxsize=1)
        self.prune()
        threading.Thread(target=self.write_jobs,daemon=True).start()

    def reset(self):
        self.flush();self.ring.clear();self.last_frame=-100.

    def push(self,top,record):
        if record['monotonic']-self.last_frame<.10:return
        self.last_frame=record['monotonic']
        item=(top,dict(record))
        self.ring.append(item)
        if self.pending:
            self.pending['frames'].append(item)
            if record['monotonic']>=self.pending['deadline']:self.flush()

    def mark(self,reason,metadata):
        now=time.perf_counter()
        if self.pending or now-self.last_mark<3 or self.count>=10:return
        self.last_mark=now;self.count+=1
        self.pending=dict(reason=reason,metadata=dict(metadata),frames=list(self.ring),deadline=now+.3)

    def flush(self):
        if not self.pending:return
        packet=self.pending;self.pending=None
        try:self.jobs.put_nowait(packet)
        except queue.Full:self.event('diagnostic_skipped',reason='保存队列已满')

    def write_jobs(self):
        while True:
            packet=self.jobs.get()
            try:self.save(packet)
            except Exception as exc:self.event('diagnostic_error',reason=str(exc))
            finally:
                self.jobs.task_done()
                del packet

    def save(self,packet):
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        directory=self.root/stamp;directory.mkdir(parents=True,exist_ok=False)
        records=[]
        for i,(top,record) in enumerate(packet['frames']):
            name=f'{i:03d}.jpg'
            ok,data=cv2.imencode('.jpg',top,[cv2.IMWRITE_JPEG_QUALITY,85])
            if not ok:raise RuntimeError('无法编码诊断图片')
            (directory/name).write_bytes(data.tobytes())
            records.append(dict(file=name,**record))
        (directory/'frames.json').write_text(json.dumps(dict(reason=packet['reason'],metadata=packet['metadata'],frames=records),ensure_ascii=False,indent=2),encoding='utf-8')
        self.prune()
        self.event('diagnostic_saved',path=str(directory),reason=packet['reason'],frames=len(records))

    def prune(self):
        """Only generated direct-child evidence groups; never other folders."""
        root=self.root.resolve()
        if not root.exists():return
        groups=[]
        for path in root.iterdir():
            if path.is_symlink() or not path.is_dir() or not re.fullmatch(r'\d{8}T\d{6}_\d{6}Z',path.name):continue
            if path.resolve().parent!=root:continue
            try:metadata=json.loads((path/'frames.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            if metadata.get('reason') not in ('candidate_trigger','absence_reset'):continue
            if any(p.is_symlink() for p in path.rglob('*')):continue
            groups.append((path,sum(p.stat().st_size for p in path.rglob('*') if p.is_file())))
        groups.sort(key=lambda item:item[0].name)
        total=sum(n for _,n in groups)
        while groups and (len(groups)>2 or total>32*1024*1024):
            path,size=groups.pop(0)
            if path.resolve().parent!=root:raise ValueError('Unsafe diagnostic cleanup path')
            shutil.rmtree(path)
            total-=size
            self.event('diagnostic_pruned',group=path.name,bytes=size)
