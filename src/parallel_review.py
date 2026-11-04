"""Bounded same-frame UI work and a read-only newer-frame veto lane.

No input, process-exit or network operations. Only the original decision owner
may accept results. Pending/unreviewed frames never become death evidence.
"""
from concurrent.futures import ThreadPoolExecutor
from collections import deque
import threading,time

_pool=None
_pool_lock=threading.Lock()
def pool():
    global _pool
    with _pool_lock:
        if _pool is None:_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='FrameUIReview')
        return _pool

def timed(call):
    start=time.perf_counter();value=call()
    return value,(time.perf_counter()-start)*1000

def review_ui(interaction,inventory,settings=None):
    """Three independent modules, same immutable frame, one bounded merge."""
    start=time.perf_counter();a=pool().submit(timed,interaction);b=pool().submit(timed,inventory)
    error=None;setting=None;setting_ms=0.
    try:
        if settings:setting,setting_ms=timed(settings)
    except BaseException as exc:error=exc
    results=[]
    for future in (a,b):
        try:results.append(future.result())
        except BaseException as exc:
            results.append((None,0.));error=error or exc
    if error:raise error
    return results[0][0],results[1][0],setting,dict(used=True,
        interaction_ms=round(results[0][1],3),inventory_ms=round(results[1][1],3),
        settings_ms=round(setting_ms,3),wall_ms=round((time.perf_counter()-start)*1000,3))

def layout_key(layout):
    return tuple(layout.get(k) for k in ('left','right','top','width','height')) if layout else None

def menu_kind(features):
    kinds=('pause','settings','inventory','confirmation','console')
    kind=max(kinds,key=lambda k:features.get(k,0))
    return kind if features.get(kind,0)>=.55 else None

class TitleTicket:
    def __init__(self,detector,frame,full_size,layout):
        self.source=frame
        self.layout=dict(layout) if layout else None;self.key=layout_key(self.layout)
        self.future=pool().submit(timed,lambda:detector.detect(frame,full_size,self.layout))
    def finish(self):return self.future.result()
    def matches(self,layout,frame=None):return self.key==layout_key(layout) and (frame is None or frame is self.source)

class ReviewTicket:
    def __init__(self,call,layout,frame=None):
        self.source=frame;self.key=layout_key(layout);self.future=pool().submit(timed,call)
    def finish(self):return self.future.result()
    def matches(self,layout,frame=None):return self.key==layout_key(layout) and (frame is None or frame is self.source)

class HudContinuity:
    """Observe positive HUD/menu recovery while complete review is running.

    This lane can veto an older negative result, never authorize a death.
    """
    def __init__(self,capture,owner,layout=None,epoch=0,assets=None,probe=None,on_suspect=None):
        self.capture=capture;self.owner=owner;self.lock=threading.Lock();self.probe_lock=threading.Lock()
        self.layout=None;self.epoch=epoch;self.positives=deque(maxlen=16)
        self.closed=threading.Event();self.checked=0;self.errors=0
        self.on_suspect=on_suspect;self.candidate=False
        if probe is None:
            from detector import Detector
            self.detector=Detector(assets);self.probe=self._inspect
        else:self.probe=probe
        self.set_layout(layout,epoch)
        self.thread=threading.Thread(target=self._run,name='FreshHUDContinuity',daemon=True);self.thread.start()
    def set_layout(self,layout,epoch):
        layout={k:layout[k] for k in ('left','right','top','width','height')} if layout else None
        with self.lock:
            if layout_key(layout)!=layout_key(self.layout) or epoch!=self.epoch:
                self.positives.clear();self.candidate=False
            self.layout=layout;self.epoch=epoch
    def _inspect(self,frame,layout):
        from hud_layout import project
        from detector import start_hud_evidence
        with self.probe_lock:
            top,menu,_,_=project(frame,layout);self.detector.scores_top(top)
            hud=start_hud_evidence(self.detector.last_features)
            score=self.detector.menu_score_top(menu)
        menu.flags.writeable=False
        return dict(hud=hud,menu=score>=.55,menu_kind=menu_kind(self.detector.last_menu_features),menu_top=menu)
    def inspect(self,frame,layout):return self.probe(frame,layout)
    def veto(self,source_time,epoch,expected_menu=None):
        with self.lock:
            return next((dict(p) for p in reversed(self.positives)
                         if p['epoch']==epoch and p['source_time']>source_time and
                         (p.get('hud') or (p.get('menu') and (expected_menu is None or p.get('menu_kind')!=expected_menu)))),None)
    def _run(self):
        sequence=0
        while not self.closed.is_set():
            snapshot=self.capture.next_frame(sequence,.03)
            if snapshot is None:continue
            sequence=snapshot.sequence
            with self.lock:layout=self.layout;epoch=self.epoch
            if not layout or not snapshot.safe or snapshot.guard_epoch!=epoch or not self.owner():continue
            if not 0<=time.perf_counter()-snapshot.captured_at<=.2:continue
            if snapshot.region[:2]!=(0,0) or snapshot.region[2]!=layout['width']:continue
            needed=layout['top']+round(240*min((layout['right']-layout['left'])/1920,(layout['height']-2*layout['top'])/1080))
            if snapshot.image.shape[0]<needed:continue
            try:
                evidence=self.probe(snapshot.image,layout);self.checked+=1
                if not self.owner() or snapshot.guard_epoch!=self.capture.epoch:continue
                if evidence.get('hud') or evidence.get('menu'):
                    self.candidate=False
                    with self.lock:self.positives.append(dict(sequence=sequence,source_time=snapshot.captured_at,
                        epoch=epoch,hud=bool(evidence.get('hud')),menu=bool(evidence.get('menu')),menu_kind=evidence.get('menu_kind')))
                else:
                    if not self.candidate and self.on_suspect:
                        self.on_suspect(dict(sequence=sequence,source_monotonic=snapshot.captured_at,guard_epoch=epoch))
                    self.candidate=True;self.capture.request_full(.35)
            except Exception:self.errors+=1
    def close(self):
        self.closed.set()
        if threading.current_thread() is not self.thread:self.thread.join(timeout=.4)
