"""Causal darkening evidence; no input/process/network APIs.

Original implementation using intensity/dark-pixel statistics, inspired by
PySceneDetect's threshold detector and FFmpeg blackdetect. Not a scene-cut
library integration, and never an independent authorization to exit.
"""
import cv2
import numpy as np
from dark_trend_parameters import PARAMETERS,SELECTION_ID

LEGACY_PARAMETERS=dict(pixel=24.,max_mean=32.,coverage=.8,gain=.8,regions=4.,increase=.1,trend=.05,n=.1,p90_gain=.9,mode=0.)

class DarkTrendWatch:
    MIN_SECONDS=.100
    def __init__(self,parameters=None):
        self.parameters=dict(PARAMETERS if parameters is None else parameters)
        self.MIN_SECONDS=float(self.parameters['n']);self.reset('initial')
    def clear_proof(self,reason):
        self.first=None;self.first_mean=None;self.count=0;self.reviewed=None;self.previous=None;self.proof_reason=reason
    def reset(self,reason):
        self.key=None;self.last=None;self.reference=None;self.reference_time=None;self.peaks=[]
        self.clear_proof(reason)
    def restart_observation(self,reason):
        self.clear_proof(reason)
        self.peaks=[] if self.reference is None else [(self.reference_time,self.reference)]
    @staticmethod
    def measure(frame):
        h,w=frame.shape[:2]
        small=cv2.resize(frame[round(h*.30):round(h*.86),round(w*.08):round(w*.92),:3],(192,96),interpolation=cv2.INTER_AREA)
        gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
        cdf=np.cumsum(np.bincount(gray.ravel(),minlength=256))
        return dict(mean=float(gray.mean()),p90=float(np.percentile(gray,90)),dark=float((gray<24).mean()),cdf=cdf.tolist(),pixels=int(gray.size),
            tiles=[float(gray[y:y+48,x:x+64].mean()) for y in (0,48) for x in (0,64,128)])
    def candidate(self,now,frame,**context):return self.candidate_stats(now,self.measure(frame),**context)
    def candidate_stats(self,now,stats,*,allowed,absent,key):
        if key!=self.key or (self.last is not None and now<=self.last):
            self.reset('epoch_or_time_changed');self.key=key
        elif self.last is not None and now-self.last>.25:self.clear_proof('full_frame_gap')
        self.last=now
        age=None if self.reference_time is None else now-self.reference_time
        result=dict(certified=False,reason='dark_trend_watching',path='dark_trend',required_ms=self.MIN_SECONDS*1000,
            reference_age_ms=None if age is None else round(age*1000,3),current=stats,proof_reset_reason=self.proof_reason)
        if not allowed:
            self.reset('context_guarded');self.key=key;self.last=now
            return dict(result,reason='dark_trend_context_guarded')
        if not absent:
            self.clear_proof('hud_or_menu_visible')
            return dict(result,reason='dark_trend_hud_visible')
        if self.reference is None or age is None or not 0<age<=1.:
            self.first=None;self.count=0
            return dict(result,reason='dark_trend_reference_unavailable')
        p=self.parameters
        self.peaks.append((now,stats));self.peaks=[entry for entry in self.peaks if now-entry[0]<=.6]
        peak_time,peak=max(self.peaks,key=lambda entry:entry[1]['mean'])
        ref=peak if p['mode']>=2 else self.reference
        threshold=float(p['pixel']) if p['pixel']>0 else float(np.clip(-p['pixel']*ref['p90'],12.,128. if p['mode']>=2 else 64.))
        pixel_bin=max(0,min(255,int(np.ceil(threshold))-1))
        def fraction(row):
            if 'cdf' in row:return row['cdf'][pixel_bin]/float(row.get('pixels',192*96))
            # Legacy unit/old diagnostic records only carry the <24 statistic.
            return row['dark'] if threshold==24. else None
        dark=fraction(stats);reference_dark=fraction(ref)
        if dark is None or reference_dark is None:
            self.first=None;self.count=0
            return dict(result,reason='dark_trend_histogram_unavailable')
        tiles=sum(b<=a*.9 for a,b in zip(ref['tiles'],stats['tiles']) if a>=4)
        previous=self.previous if self.previous is not None else self.reference['mean']
        falling=stats['mean']<previous-.15
        rising=stats['mean']>previous*1.03+.3
        self.previous=stats['mean']
        strong=(ref['mean']>=6 and stats['mean']<=p['max_mean'] and dark>=p['coverage'] and dark-reference_dark>=p['increase']
            and stats['mean']<=ref['mean']*p['gain'] and stats['p90']<=ref['p90']*p['p90_gain'] and tiles>=p['regions'])
        ongoing=stats['mean']<=ref['mean']*.995 if p['mode']>0 else strong
        if rising or not ongoing:self.first=None;self.first_mean=None;self.count=0
        if ongoing and not rising:
            if self.first is None and (falling or p['mode']==0):self.first=now;self.first_mean=stats['mean']
            if self.first is not None:self.count+=1
        span=0. if self.first is None else now-self.first
        trend_drop=0. if self.first_mean is None else 1.-stats['mean']/max(1.,self.first_mean)
        certified=(strong and not rising and self.count>=2 and span>=self.MIN_SECONDS-1e-6 and falling and trend_drop>=p['trend'] and self.first_mean-stats['mean']>=1.25
            and self.reviewed is not None and 0<now-self.reviewed<=.5)
        return dict(result,certified=bool(certified),reason='dark_trend_confirmed' if certified else 'dark_trend_waiting' if self.first is not None else 'dark_trend_insufficient',
            gain=round(stats['mean']/max(1.,ref['mean']),4),dark_fraction_increase=dark-reference_dark,
            pixel_threshold=threshold,dark_fraction=dark,reference_dark_fraction=reference_dark,condition_ready=bool(strong),
            comparison='rolling_peak' if p['mode']>=2 else 'last_live',comparison_age_ms=round((now-peak_time)*1000,3) if p['mode']>=2 else result['reference_age_ms'],selection_id=SELECTION_ID,
            falling_regions=tiles,falling=falling,trend_drop=round(trend_drop,4),samples=self.count,observed_ms=round(span*1000,3))
    def reviewed_frame(self,now,frame,**context):return self.reviewed_stats(now,self.measure(frame),**context)
    def reviewed_stats(self,now,stats,*,visible,absent,key,allowed):
        if not allowed:
            self.reset('full_review_guarded');self.key=key;self.last=now;return
        if key!=self.key:self.reset('epoch_changed');self.key=key;self.last=now
        if visible:
            self.reference=stats;self.reference_time=now;self.clear_proof('visible_reference_saved')
            self.previous=stats['mean'];self.peaks=[(now,stats)]
        elif absent:self.reviewed=now
