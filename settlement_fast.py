"""Independent log-triggered visual consumer; no process/input APIs here."""
import threading,time,traceback
import cv2,numpy as np
from detector import Detector,start_hud_evidence
from hud_layout import project
from decision import settlement_context_eligible
from visual_absence import black_scene
from probe_context import checked_layout,snapshot_context,finite


class FastProbe:
    def __init__(self,assets=None):self.detector=Detector(assets)

    @staticmethod
    def possible_title(frame,layout):
        layout,reason=checked_layout(dict(layout=layout),frame)
        if frame is None:reason='missing_frame'
        if reason:return True
        left,right,y=layout['left'],layout['right'],layout['top']
        width=right-left;height=layout['height']-2*y
        crop=frame[y+round(height*.04):y+round(height*.43),left+round(width*.18):left+round(width*.82),:3]
        if not crop.size:return True
        gray=cv2.cvtColor(cv2.resize(crop,(480,176)),cv2.COLOR_BGR2GRAY)
        edge=cv2.Canny(gray,35,90)
        joined=cv2.morphologyEx(edge,cv2.MORPH_CLOSE,np.ones((3,13),np.uint8))
        for c in cv2.findContours(joined,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            x,y,w,h=cv2.boundingRect(c)
            if w>=140 and h>=8 and y<100 and float(np.mean(edge[y:y+h,x:x+w]>0))>=.015:return True
        return False

    def __call__(self,frame,context):
        start=time.perf_counter();d=self.detector;layout,reason=checked_layout(context,frame)
        if frame is None:reason='missing_frame'
        if reason:return dict(features=dict(combat_score=0.,body_shape=0.,bar_frames=0.,label_shape=0.),
            menu_score=1.,cue={},visual_path='guarded',guard_reason=reason,probe_ms=(time.perf_counter()-start)*1000)
        top,menu,extract,_=project(frame,layout)
        d.scores_top(top);features=dict(d.last_features)
        menu_score=d.menu_score_top(menu);cue=d.extraction_features(extract)
        path='visible_hud_or_menu';scene=None
        if not start_hud_evidence(features) and menu_score<.55:
            scene=black_scene(frame,(layout['width'],layout['height']),layout)
            if scene['certified']:
                path='certified_black_scene'
            else:
                path='full_ui_review'
                interaction=d.interaction_ui.detect(frame,(layout['width'],layout['height']),layout)
                if interaction.get('steam') or interaction.get('btr'):
                    return dict(features=features,menu_score=1.,cue=cue,top=top,visual_path=path,
                                probe_ms=(time.perf_counter()-start)*1000)
                inventory=d.inventory_ui.detect(frame,(layout['width'],layout['height']),layout,
                    return_score=d.last_menu_features.get('inventory_return',0.))
                if inventory.get('present'):menu_score=max(menu_score,.95)
                # Any visible title/layout ambiguity still takes the original path.
                if menu_score<.55 and self.possible_title(frame,layout):menu_score=.55
        return dict(features=features,menu_score=menu_score,cue=cue,top=top,
                    visual_path=path,black_scene=scene,probe_ms=(time.perf_counter()-start)*1000)


class ParallelSettlementCheck:
    def __init__(self,capture,watch,probe,owner,on_confirm,report):
        self.capture=capture;self.watch=watch;self.probe=probe;self.owner=owner
        self.on_confirm=on_confirm;self.report=report;self.context=None
        self.lock=threading.Lock();self.closed=threading.Event();self.handled=set()
        self.thread=threading.Thread(target=self._run,name='ParallelSettlementVisual',daemon=True)

    def publish(self,context):
        with self.lock:self.context=snapshot_context(context)

    def current_context(self):
        with self.lock:return snapshot_context(self.context) if self.context else None

    def start(self):self.thread.start()

    def _run(self):
        while not self.closed.wait(.005):
            hint=self.watch.peek()
            if not hint:continue
            token=(hint['session'],hint['request_id'])
            if token in self.handled:continue
            self.handled.add(token)
            try:self._attempt(hint)
            except Exception as exc:
                self.report(dict(request_id=hint['request_id'],status='error',error_type=type(exc).__name__,error_stage='independent_visual',
                                 error_message=str(exc)[:240],
                                 error_frames=[dict(file=f.filename.replace(chr(92),'/').rsplit('/',1)[-1],function=f.name,line=f.lineno)
                                               for f in traceback.extract_tb(exc.__traceback__)[-5:]]))
            finally:
                if hasattr(self.capture,'stop_full_burst'):self.capture.stop_full_burst()

    def _attempt(self,hint):
        trace=[];proof=[];sequence=0;status='deferred';reason='no_fresh_confirmation'
        if not isinstance(hint,dict) or not finite(hint.get('seen_monotonic')):
            self.report(dict(status='deferred',reason='invalid_hint_metadata'));return
        self.capture.request_full(1.)
        origin=self.current_context();attempt_epoch=origin.get('guard_epoch') if origin else None
        while not self.closed.is_set() and time.perf_counter()-hint['seen_monotonic']<=1.:
            if not self.owner():reason='owner_or_session_changed';break
            context=self.current_context()
            if not context or not context.get('active'):reason=(context or {}).get('invalid_reason','no_active_context');break
            snapshot=self.capture.next_frame(sequence,.03)
            if snapshot is None:continue
            if not finite(getattr(snapshot,'captured_at',None)) or not isinstance(getattr(snapshot,'sequence',None),int):
                reason='invalid_capture_metadata';break
            sequence=snapshot.sequence
            # Two distinct *newly captured* frames, both after the hint. Never
            # treat re-reading a cached image as another observation.
            if snapshot.captured_at<hint['seen_monotonic']:continue
            if not snapshot.safe or snapshot.guard_epoch!=context.get('guard_epoch') or snapshot.guard_epoch!=attempt_epoch:
                reason='capture_safety_changed';break
            if snapshot.region!=tuple(context['full_region']):continue
            observed=self.probe(snapshot.image,context)
            candidate=dict(hint,full_frame=True,guard_epoch=snapshot.guard_epoch)
            allowed=settlement_context_eligible(snapshot.captured_at,observed['features'],
                observed['menu_score'],observed['cue'],candidate,context)
            trace.append(dict(sequence=sequence,captured_at=snapshot.captured_at,
                captured_wall_ns=snapshot.wall_ns,
                probe_ms=round(observed['probe_ms'],3),features=observed['features'],
                visual_path=observed.get('visual_path'),
                scene=observed.get('black_scene'),
                menu_score=observed['menu_score'],eligible=allowed))
            trace[-1].update(context_age_ms=round((snapshot.captured_at-context['observed_at'])*1000,3)
                if context.get('observed_at') is not None else None,
                last_combat=context.get('last_combat'),last_menu=context.get('last_menu'),
                protected=context.get('protected'),protected_until=context.get('protected_until'),
                guard_epoch=snapshot.guard_epoch)
            trace=trace[-8:]
            if not allowed:proof=[];continue
            if proof and (snapshot.captured_at-proof[-1][0].captured_at>.2 or
                          snapshot.captured_at-proof[-1][0].captured_at<.015):proof=[]
            proof.append((snapshot,observed))
            if len(proof)<2:continue
            if time.perf_counter()-snapshot.captured_at>.2:reason='probe_finished_too_late';break
            context=self.current_context()
            if not self.owner() or not context or not settlement_context_eligible(snapshot.captured_at,
                    observed['features'],observed['menu_score'],observed['cue'],candidate,context):
                reason='context_changed_before_dispatch';break
            if self.closed.is_set():reason='checker_stopped';break
            accepted=self.on_confirm(dict(hint=hint,frame_time=snapshot.captured_at,
                guard_epoch=snapshot.guard_epoch,features=observed['features'],
                menu_score=observed['menu_score'],cue=observed['cue'],proof=proof))
            status='confirmed' if accepted else 'deferred';reason='dispatched' if accepted else 'normal_path_won_or_guarded'
            break
        self.report(dict(request_id=hint['request_id'],status=status,reason=reason,
            read_to_finish_ms=round((time.perf_counter()-hint['seen_monotonic'])*1000,3),
            visual_path=trace[-1].get('visual_path') if trace else None,frames=trace))

    def close(self):
        self.closed.set()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:self.thread.join(timeout=.3)
