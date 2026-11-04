"""HSV temporal evidence only; no capture, input, process or network APIs."""
from collections import deque
import cv2
import numpy as np


class HsvFadeWatch:
    MIN_SECONDS = .100  # Selected by offline sweep; see validation report.
    ENABLED = True

    def __init__(self, seconds=None):
        self.seconds = self.MIN_SECONDS if seconds is None else max(0.,float(seconds))
        self.reset('initial')

    def clear_proof(self, reason):
        self.first=None;self.samples=0;self.reviewed=None;self.previous=None
        self.peaks=deque();self.proof_reason=reason

    def reset(self, reason):
        self.key=None;self.last=None;self.references=deque();self.baseline=None
        self.started=None;self.clear_proof(reason)

    @staticmethod
    def measure(frame):
        h,w=frame.shape[:2]
        small=cv2.resize(frame[round(h*.30):round(h*.86),round(w*.08):round(w*.92),:3],
                         (192,96),interpolation=cv2.INTER_AREA)
        hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
        v=hsv[:,:,2];s=hsv[:,:,1].astype('float32')/255.
        valid=(v>=24)&(v<=240);coverage=float(valid.mean())
        return dict(value=[float(x) for x in np.percentile(v,(50,75,90))],coverage=coverage,
                    saturation=float(np.median(s[valid])) if coverage>=.15 else None,
                    gray_fraction=float((s[valid]<.10).mean()) if coverage>=.15 else None)

    def bind(self,now,key):
        if key!=self.key or (self.last is not None and now<=self.last):
            self.reset('epoch_or_time_changed');self.key=key
        elif self.last is not None and now-self.last>.25:
            # An unseen gap is never observation time or a reusable UI review.
            self.clear_proof('full_frame_gap')
        self.last=now
        while self.references and now-self.references[0][0]>.8:self.references.popleft()

    def candidate(self,now,frame,**context):
        return self.candidate_stats(now,self.measure(frame),**context)

    def candidate_stats(self,now,stats,*,allowed,absent,key):
        self.bind(now,key)
        result=dict(certified=False,reason='hsv_watching',path='hsv_fade',required_ms=round(self.seconds*1000,3),
                    current=stats,proof_reset_reason=self.proof_reason,reference_count=len(self.references),
                    reference_age_ms=None if not self.references else round((now-self.references[-1][0])*1000,3))
        if not self.ENABLED or not allowed:
            self.reset('context_guarded');self.key=key;self.last=now
            return dict(result,reason='hsv_context_guarded')
        if not absent:
            self.started=None;self.baseline=None;self.clear_proof('hud_or_menu_visible')
            return dict(result,reason='hsv_hud_or_menu_visible')
        if self.started is None:
            self.started=now
            refs=[s for _,s in self.references if s['saturation'] is not None]
            if len(refs)>=2:
                self.baseline=dict(saturation=float(np.median([s['saturation'] for s in refs])),
                    gray_fraction=float(np.median([s['gray_fraction'] for s in refs])))
                self.peaks.extend((t,s['value']) for t,s in self.references)
        if self.baseline is None or not self.references or now-self.references[-1][0]>1.:
            self.first=None;self.samples=0
            return dict(result,reason='hsv_reference_unavailable')
        self.peaks.append((now,stats['value']))
        while self.peaks and now-self.peaks[0][0]>.6:self.peaks.popleft()
        peak=max((v for _,v in self.peaks),key=lambda v:sum(v))
        usable=[i for i,v in enumerate(peak) if v>=16]
        ratios=[stats['value'][i]/peak[i] for i in usable]
        gain=float(np.median(ratios)) if len(ratios)>=2 else None
        sat=stats['saturation'];base=self.baseline['saturation']
        sat_ratio=sat/base if sat is not None and base>=.12 else None
        gray_delta=(stats['gray_fraction']-self.baseline['gray_fraction']) if sat is not None else None
        falling=self.previous is not None and sum(stats['value'])<sum(self.previous)-1.
        rising=self.previous is not None and sum(stats['value'])>sum(self.previous)*1.03+1.
        self.previous=list(stats['value'])
        strong=(gain is not None and gain<=.65 and max(ratios)<=.75 and sat_ratio is not None
                and sat_ratio<=.65 and sat<=.12 and gray_delta>=.15 and not rising)
        result.update(gain=None if gain is None else round(gain,4),peak=peak,
                      saturation_ratio=None if sat_ratio is None else round(sat_ratio,4),
                      baseline=self.baseline,gray_fraction_delta=gray_delta,falling=falling)
        if not strong:
            self.first=None;self.samples=0
            return dict(result,reason='hsv_insufficient_fade')
        if self.first is None:self.first=now
        self.samples+=1
        span=now-self.first
        certified=(self.samples>=2 and span>=self.seconds-1e-6 and falling
                   and self.reviewed is not None and 0<now-self.reviewed<=.5)
        return dict(result,certified=bool(certified),reason='hsv_fade_confirmed' if certified else 'hsv_waiting_fresh_frames',
                    samples=self.samples,observed_ms=round(span*1000,3),
                    reviewed_age_ms=None if self.reviewed is None else round((now-self.reviewed)*1000,3))

    def reviewed_frame(self,now,frame,**context):
        self.reviewed_stats(now,self.measure(frame),**context)

    def reviewed_stats(self,now,stats,*,visible,absent,key,allowed):
        if not allowed:
            self.reset('full_review_guarded');self.key=key;self.last=now;return
        if key!=self.key:self.reset('epoch_changed');self.key=key;self.last=now
        if visible:
            self.references.append((now,stats))
            while self.references and now-self.references[0][0]>.8:self.references.popleft()
            self.started=None;self.baseline=None;self.clear_proof('visible_reference_saved')
        elif absent:self.reviewed=now
