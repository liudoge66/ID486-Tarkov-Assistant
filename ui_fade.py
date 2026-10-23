"""Observe matched UI/scene regions fading together; brightness alone is not evidence."""
import cv2
import numpy as np

class UniformFadeWatch:
    def __init__(self):self.reset()
    def reset(self):self.reference=None;self.since=None;self.falls=0;self.last_gain=1.;self.context=None
    def update(self,now,top,context=None,suppress=False):
        if top is None or not context or suppress:
            self.reset();return dict(confirmed=False,reason='inactive_or_user_transition')
        if context!=self.context:self.reset();self.context=context
        gray=cv2.cvtColor(top[:,:,:3],cv2.COLOR_BGR2GRAY)
        boxes=([(360,65,760,235),(760,65,1160,235),(1160,65,1560,235)] if context=='esc'
               else [(565,1,1020,80),(1020,1,1500,80),(200,110,1500,230)])
        patches=[cv2.resize(gray[y:y2,x:x2],(100,32)).astype('float32') for x,y,x2,y2 in boxes]
        if self.reference is None:
            self.reference=patches;self.since=now;return dict(confirmed=False,reason='baseline',context=context)
        gains=[];errors=[]
        for base,current in zip(self.reference,patches):
            mask=base>max(12,float(np.percentile(base,50)))
            if mask.sum()<60 or float(np.std(base))<4:
                self.reset();return dict(confirmed=False,reason='insufficient_structure')
            a=base[mask];b=current[mask];gain=float(np.median(b/(a+1e-3)))
            error=float(np.sqrt(np.mean((b-gain*a)**2))/(float(a.mean())+1))
            gains.append(gain);errors.append(error)
        gain=float(np.median(gains));elapsed=now-self.since
        # Opening fade-in and local lighting changes continuously refresh the baseline.
        matched=max(errors)<.12 and max(gains)-min(gains)<.16
        if gain>=.98 or not matched:
            self.reference=patches;self.since=now;self.falls=0;self.last_gain=1.
            return dict(confirmed=False,reason='baseline_updated',gains=gains,errors=errors,context=context)
        self.falls=self.falls+1 if gain<=self.last_gain+.015 else 0;self.last_gain=gain
        confirmed=matched and .22<gain<=.80 and elapsed>=.08 and (self.falls>=2 or gain<=.60)
        return dict(confirmed=bool(confirmed),reason='uniform_ui_fade' if confirmed else 'watching_fade',
                    context=context,gain=round(gain,4),gains=[round(x,4) for x in gains],errors=[round(x,4) for x in errors],
                    elapsed=round(elapsed,4),falling_samples=self.falls)
