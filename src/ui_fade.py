"""Observe matched UI/scene regions fading together; brightness alone is not evidence."""
import cv2
import numpy as np

class UniformFadeWatch:
    def __init__(self):self.reset()
    def reset(self):self.reference=None;self.since=None;self.falls=0;self.last_gain=1.;self.context=None;self.last_drop=None;self.world_context=None;self.world_box=None
    def update(self,now,top,context=None,suppress=False,world=None):
        if top is None or not context or suppress:
            self.reset();return dict(confirmed=False,reason='inactive_or_user_transition')
        use_world=context=='combat' and world is not None
        if context!=self.context or use_world!=self.world_context:
            self.reset();self.context=context;self.world_context=use_world
        gray=cv2.cvtColor(top[:,:,:3],cv2.COLOR_BGR2GRAY)
        boxes=([(800,2,1160,65),(480,65,1030,125),(1680,2,1900,110)] if context=='settings' else [(360,65,760,235),(760,65,1160,235),(1160,65,1560,235)] if context=='esc'
               else [(565,1,1020,80),(1020,1,1340,80),(200,110,1500,230)])
        patches=[cv2.resize(gray[y:y2,x:x2],(100,32)).astype('float32') for x,y,x2,y2 in boxes]
        if use_world:
            # The former fixed background strip can be a flat roof/wall even
            # when the actual game world is textured. Keep both HUD anchors
            # and compare the independently captured central world instead.
            scene=world if world.ndim==2 else cv2.cvtColor(world[:,:,:3],cv2.COLOR_BGR2GRAY)
            if self.world_box is None:
                h,w=scene.shape
                choices=[(0,0,w//2,h*3//4),(w//4,0,w*3//4,h*3//4),(w//2,0,w,h//2)]
                self.world_box=choices[0]
                for box in choices:
                    x,y,x2,y2=box;patch=cv2.resize(scene[y:y2,x:x2],(100,32))
                    if float(patch.std())>=4 and np.count_nonzero(patch>max(12,float(np.percentile(patch,50))))>=60:
                        self.world_box=box;break
            x,y,x2,y2=self.world_box
            patches[2]=cv2.resize(scene[y:y2,x:x2],(100,32)).astype('float32')
        if self.reference is None:
            self.reference=patches;self.since=now;return dict(confirmed=False,reason='baseline',context=context)
        gains=[];errors=[];correlations=[]
        for index,(base,current) in enumerate(zip(self.reference,patches)):
            mask=base>max(12,float(np.percentile(base,50)))
            if mask.sum()<60 or float(np.std(base))<4:
                details=dict(confirmed=False,reason='insufficient_structure',region=index,
                    reference_std=round(float(np.std(base)),4),bright_pixels=int(mask.sum()),
                    world_observed=bool(use_world),world_box=self.world_box)
                self.reset();return details
            a=base[mask];b=current[mask];gain=float(np.median(b/(a+1e-3)))
            error=float(np.sqrt(np.mean((b-gain*a)**2))/(float(a.mean())+1))
            gains.append(gain);errors.append(error)
            # Brightness matching alone can accept a changed, low-contrast scene.
            # Require the spatial pattern to survive the dimming as well.
            centered_a=a-a.mean();centered_b=b-b.mean()
            denom=float(np.linalg.norm(centered_a)*np.linalg.norm(centered_b))
            correlations.append(float(np.dot(centered_a,centered_b))/denom if denom>1e-6 else 0.)
        gain=float(np.median(gains));elapsed=now-self.since
        # Opening fade-in and local lighting changes continuously refresh the baseline.
        matched=max(errors)<.12 and max(gains)-min(gains)<.16 and (context!='combat' or min(correlations)>=.80)
        if gain>=.98 or not matched:
            self.reference=patches;self.since=now;self.falls=0;self.last_gain=1.;self.last_drop=None
            return dict(confirmed=False,reason='baseline_updated',gains=gains,errors=errors,context=context)
        if gain<self.last_gain-.003:self.falls+=1;self.last_drop=now
        elif gain>self.last_gain+.015:self.falls=0
        self.last_gain=gain
        confirmed=matched and .22<gain<=.80 and  .08<=elapsed<=1.5 and self.last_drop is not None and now-self.last_drop<=.2 and (self.falls>=2 or gain<=.60)
        return dict(confirmed=bool(confirmed),matched=bool(matched),world_observed=bool(use_world),world_box=self.world_box,reason='uniform_ui_fade' if confirmed else 'watching_fade',
                    context=context,gain=round(gain,4),gains=[round(x,4) for x in gains],errors=[round(x,4) for x in errors],
                    elapsed=round(elapsed,4),falling_samples=self.falls,correlations=[round(x,4) for x in correlations])
