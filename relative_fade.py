"""Relative death-fade observation. No capture, input, process or network APIs."""
import cv2
import numpy as np

class RelativeFadeWatch:
    RATIO=.70
    MIN_SECONDS=.10
    def __init__(self):self.reset()
    def reset_proof(self,reason):
        self.reviewed=None;self.first=None;self.samples=0
        self.last_gain=1.;self.last_drop=None;self.proof_reset_reason=reason
    def reset(self,reason='initial'):
        self.key=None;self.reference=None;self.reference_time=None;self.last=None
        self.reference_reset_reason=reason;self.gap_ms=None
        self.reset_proof(reason)
    @staticmethod
    def preview(frame):
        h,w=frame.shape[:2]
        # Six world regions, away from the top HUD and bottom controls.
        small=cv2.resize(frame[round(h*.30):round(h*.86),round(w*.08):round(w*.92),:3],(192,96),interpolation=cv2.INTER_AREA)
        return cv2.cvtColor(small,cv2.COLOR_BGR2GRAY).astype('float32')
    def bind(self,now,key):
        self.gap_ms=None
        if key!=self.key:
            self.reset('capture_epoch_or_layout_changed');self.key=key
        elif self.last is not None and now<=self.last:
            self.reset('nonmonotonic_or_duplicate_frame');self.key=key
        elif self.last is not None and now-self.last>.25:
            # A gap invalidates continuous proof, not a still-fresh visible
            # reference. The first resumed frame must be fully reviewed again.
            self.gap_ms=round((now-self.last)*1000,3)
            self.reset_proof('full_frame_gap')
        self.last=now
    def compare(self,now,frame):
        age=None if self.reference_time is None else now-self.reference_time
        audit=dict(reference_age_ms=None if age is None else round(age*1000,3),
                   reference_reset_reason=self.reference_reset_reason,
                   proof_reset_reason=self.proof_reset_reason,full_frame_gap_ms=self.gap_ms)
        if self.reference is None:
            return dict(certified=False,reason='reference_missing',**audit)
        if age is None or age<=0:
            return dict(certified=False,reason='reference_not_older_than_frame',**audit)
        if age>1.:
            self.reference=None;self.reference_time=None;self.reference_reset_reason='reference_expired'
            return dict(certified=False,reason='reference_expired',**audit)
        current=self.preview(frame);gains=[];correlations=[];errors=[]
        for row in range(2):
            for col in range(3):
                a=self.reference[row*48:(row+1)*48,col*64:(col+1)*64].ravel()
                b=current[row*48:(row+1)*48,col*64:(col+1)*64].ravel()
                if float(a.std())<4:return dict(certified=False,reason='reference_has_no_structure',**audit)
                mask=a>max(8.,float(np.percentile(a,35)))
                x=a[mask];y=b[mask]
                gain=float(np.median(y/(x+1e-3)));gains.append(gain)
                ax=a-a.mean();by=b-b.mean();denom=float(np.linalg.norm(ax)*np.linalg.norm(by))
                correlations.append(float(np.dot(ax,by))/denom if denom>1e-6 else 0.)
                errors.append(float(np.sqrt(np.mean((y-gain*x)**2)))/(float(x.mean())+1.))
        gain=float(np.median(gains))
        matched=min(correlations)>=.85 and max(errors)<=.10 and max(gains)-min(gains)<=.12
        return dict(certified=False,reason='watching_relative_fade' if matched else 'scene_structure_changed',
                    **audit,matched=bool(matched),gain=round(gain,4),gains=[round(x,4) for x in gains],
                    correlations=[round(x,4) for x in correlations],errors=[round(x,4) for x in errors],ratio=self.RATIO)
    def candidate(self,now,frame,*,allowed,absent,key):
        self.bind(now,key)
        if not allowed:
            self.reset('context_guarded');self.key=key;self.last=now
            return dict(certified=False,reason='context_guarded',reference_reset_reason=self.reference_reset_reason)
        if not absent:
            self.first=None;self.samples=0;self.reviewed=None;self.last_gain=1.;self.last_drop=None
            return dict(certified=False,reason='hud_or_menu_visible')
        result=self.compare(now,frame);gain=result.get('gain',1.)
        if gain<self.last_gain-.003:self.last_drop=now
        rising=gain>self.last_gain+.03
        self.last_gain=gain
        if not result.get('matched') or gain>self.RATIO or rising:
            self.first=None;self.samples=0
            return result
        if self.first is None:self.first=now
        self.samples+=1
        valid=(self.reviewed is not None and 0<now-self.reviewed<=.5 and
               self.samples>=2 and now-self.first>=self.MIN_SECONDS-1e-6 and
               self.last_drop is not None and now-self.last_drop<=.15)
        result.update(certified=bool(valid),reason='relative_fade_confirmed' if valid else 'waiting_fresh_relative_frames',
                      samples=self.samples,observed_ms=round((now-self.first)*1000,3),
                      reviewed_age_ms=None if self.reviewed is None else round((now-self.reviewed)*1000,3))
        return result
    def reviewed_frame(self,now,frame,*,visible,absent,key,allowed):
        if not allowed:return
        if key!=self.key:self.reset('capture_epoch_or_layout_changed');self.key=key;self.last=now
        if visible:
            self.reference=self.preview(frame);self.reference_time=now;self.reference_reset_reason='visible_full_frame_saved'
            self.first=None;self.samples=0;self.reviewed=None;self.last_gain=1.;self.last_drop=None
        elif absent:self.reviewed=now
