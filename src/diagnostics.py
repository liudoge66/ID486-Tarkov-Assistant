"""Bounded, game-only top-strip evidence. No capture or input APIs here."""
from collections import deque
from pathlib import Path
from datetime import datetime,timezone
import json,queue,threading,time,re,shutil,copy
import cv2
from edition import FENCE

class Diagnostics:
    def __init__(self,root,event):
        self.root=Path(root);self.event=event
        self.ring=deque(maxlen=20 if FENCE else 3);self.pending=None;self.count=0;self.last_mark=-100.;self.last_frame=-100.
        self.scene_ring=deque(maxlen=24);self.last_scene_sequence=None
        self.jobs=queue.Queue(maxsize=1)
        self.capture_jobs=queue.Queue(maxsize=2);self.evidence_lock=threading.RLock();self.generation=0
        self.prune()
        threading.Thread(target=self.write_jobs,daemon=True).start()
        if FENCE:threading.Thread(target=self.compress_frames,daemon=True).start()

    def reset(self):
        with self.evidence_lock:
            self.flush();self.generation+=1;self.ring.clear();self.scene_ring.clear();self.last_scene_sequence=None;self.last_frame=-100.

    def push_scene(self,frame,top,menu,record,candidate_world=None):
        """Bounded game-only evidence, saved only with an existing diagnostic mark.

        World preview is lossless; normalized gray UI strips retain exact inputs
        needed by the fade guards. Never retain a 4K capture buffer.
        """
        sequence=record.get('frame_sequence')
        if sequence is None or sequence==self.last_scene_sequence:return
        generation=self.generation
        full=record.get('full_size')
        if not full or frame.shape[:2]!=(full[1],full[0]) or not record.get('capture_safe'):return
        h,w=frame.shape[:2]
        scene=cv2.resize(frame[round(h*.30):round(h*.86),round(w*.08):round(w*.92),:3],(192,96),interpolation=cv2.INTER_AREA)
        strips=[None if a is None else cv2.cvtColor(a[:,:,:3],cv2.COLOR_BGR2GRAY) for a in (top,menu)]
        box=record.get('extraction',{}).get('center_timer',{}).get('evidence_box')
        central=None
        if box and len(box)==4:
            x0,y0,x1,y1=map(int,box);x0=max(0,x0);y0=max(0,y0);x1=min(w,x1);y1=min(h,y1)
            if x1>x0 and y1>y0:central=cv2.cvtColor(frame[y0:y1,x0:x1,:3],cv2.COLOR_BGR2GRAY)
        strips.append(central)
        strips.append(None if candidate_world is None else candidate_world.copy())
        packet=(scene,strips,copy.deepcopy(record))
        with self.evidence_lock:
            if generation!=self.generation:return
            self.last_scene_sequence=sequence;self.scene_ring.append(packet)
            if self.pending:self.pending.setdefault('scenes',[]).append(packet)

    def enqueue_parallel(self,proof,pid,metadata):
        records=[]
        for snapshot,observed in proof:
            image=snapshot.image if FENCE else observed['top']
            records.append((image,dict(monotonic=snapshot.captured_at,
                wall_time=snapshot.wall_ns/1e9,phase='parallel_settlement',game_pid=pid)))
        with self.evidence_lock:
            if self.count>=10:return
            self.count+=1
        try:self.jobs.put_nowait(dict(reason='parallel_settlement',metadata=metadata,frames=records))
        except queue.Full:self.event('diagnostic_skipped',reason='快速检查截图保存队列已满')

    def push(self,top,record):
        if record['monotonic']-self.last_frame<(.2 if FENCE else .3):return
        self.last_frame=record['monotonic']
        if FENCE:
            # Keep JPEG evidence in the ring, not 20 uncompressed 4K BGRA frames.
            try:self.capture_jobs.put_nowait((top.copy(),dict(record),self.generation))
            except queue.Full:pass
        else:self.append_frame((top.copy(),dict(record)),self.generation)

    def append_frame(self,item,generation):
        with self.evidence_lock:
            if generation!=self.generation:return
            self.ring.append(item)
            if self.pending:
                self.pending['frames'].append(item)
                if item[1]['monotonic']>=self.pending['deadline']:self.flush()

    def compress_frames(self):
        while True:
            frame,record,generation=self.capture_jobs.get()
            try:
                ok,data=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,92])
                if ok:self.append_frame((data.tobytes(),record),generation)
            finally:self.capture_jobs.task_done()

    def mark(self,reason,metadata):
        with self.evidence_lock:
            now=time.perf_counter()
            if self.pending or now-self.last_mark<3 or self.count>=10:return
            self.last_mark=now;self.count+=1
            self.pending=dict(reason=reason,metadata=dict(metadata),frames=list(self.ring),scenes=list(self.scene_ring),deadline=now+(.8 if FENCE else .3))

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
        stamp=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f%z')
        directory=self.root/stamp;directory.mkdir(parents=True,exist_ok=False)
        records=[]
        for i,(top,record) in enumerate(packet['frames']):
            local=datetime.fromtimestamp(record['wall_time']).astimezone()
            name=local.strftime('%H时%M分%S秒_')+f'{local.microsecond//1000:03d}_{i:02d}.jpg'
            record=dict(record,timestamp=local.isoformat(timespec='milliseconds'),scope='full_game_screen' if FENCE else 'top_hud')
            if isinstance(top,bytes):data=top
            else:
                ok,encoded=cv2.imencode('.jpg',top,[cv2.IMWRITE_JPEG_QUALITY,82])
                if not ok:raise RuntimeError('无法编码诊断图片')
                data=encoded.tobytes()
            (directory/name).write_bytes(data)
            records.append(dict(file=name,**record))
        scenes=[]
        for i,(world,strips,record) in enumerate(packet.get('scenes',[])):
            files={}
            for label,pixels in [('world',world),('top_gray',strips[0]),('menu_gray',strips[1]),('center_timer',strips[2] if len(strips)>2 else None),('candidate_world',strips[3] if len(strips)>3 else None)]:
                if pixels is None:continue
                name=f'scene_{i:03d}_{label}.png';ok,data=cv2.imencode('.png',pixels)
                if not ok:raise RuntimeError('无法编码场景诊断图片')
                (directory/name).write_bytes(data.tobytes());files[label]=name
            scenes.append(dict(record,files=files,scope='central_world_192x96_and_normalized_gray_ui'))
        metadata=packet['metadata'] if FENCE else {'说明':'游戏顶部界面及有限场景缩略图；是否回档需进入游戏确认'}
        (directory/'frames.json').write_text(json.dumps(dict(schema_version=3,reason=packet['reason'],metadata=metadata,frames=records if FENCE else [dict(file=r['file'],timestamp=r['timestamp'],scope=r['scope']) for r in records],scenes=scenes),ensure_ascii=False,indent=2),encoding='utf-8')
        self.prune()
        self.event('diagnostic_saved',path=str(directory),reason=packet['reason'],frames=len(records))

    def prune(self):
        """Only generated direct-child evidence groups; never other folders."""
        root=self.root.resolve()
        if not root.exists():return
        groups=[]
        for path in root.iterdir():
            if path.is_symlink() or not path.is_dir() or not re.fullmatch(r'(\d{8}T\d{6}_\d{6}Z|\d{8}_\d{6}_\d{6}[+-]\d{4})',path.name):continue
            if path.resolve().parent!=root:continue
            try:metadata=json.loads((path/'frames.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):continue
            if metadata.get('reason') not in ('candidate_trigger','absence_reset'):continue
            if any(p.is_symlink() for p in path.rglob('*')):continue
            groups.append((path,sum(p.stat().st_size for p in path.rglob('*') if p.is_file())))
        groups.sort(key=lambda item:item[0].name)
        total=sum(n for _,n in groups)
        while groups and (len(groups)>(20 if FENCE else 12) or total>(512 if FENCE else 32)*1024*1024):
            path,size=groups.pop(0)
            if path.resolve().parent!=root:raise ValueError('Unsafe diagnostic cleanup path')
            shutil.rmtree(path)
            total-=size
            self.event('diagnostic_pruned',group=path.name,bytes=size)
