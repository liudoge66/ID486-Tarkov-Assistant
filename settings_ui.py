"""Positive display-setting changes and menu countdowns, without OCR or input APIs."""
from pathlib import Path
import cv2
import numpy as np
from transfer import TransferDetector


class SettingsUI:
    def __init__(self, assets=None):
        root = Path(assets or Path(__file__).parent/'assets')
        self.templates = {n:cv2.imdecode(np.frombuffer((root/'settings'/(n+'.png')).read_bytes(),np.uint8),0)
                          for n in ('resolution_cn','display_mode_cn','save_cn','extraction_countdown_cn')}
        self._scaled_refs = {
            id(tpl): {round(float(s),4): (tpl if abs(s-1.)<1e-6 else cv2.resize(tpl,None,fx=s,fy=s))
                      for s in (.75,.85,1.,1.15,1.3)}
            for tpl in self.templates.values()
        }
        self.numbers = TransferDetector(root)
        self.masks = None
        self.pending_masks = None

    def match(self, gray, template, scales=(.75,.85,1.,1.15,1.3)):
        best = (0.,(0,0),1.,template.shape)
        cached = self._scaled_refs.get(id(template), {})
        for scale in scales:
            ref = cached.get(round(float(scale),4))
            if ref is None:
                ref = cv2.resize(template,None,fx=scale,fy=scale)
            if min(gray.shape) < 2 or gray.shape[0] < ref.shape[0] or gray.shape[1] < ref.shape[1]:
                continue
            _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(gray,ref,cv2.TM_CCOEFF_NORMED))
            if score > best[0]:best = (float(score),loc,scale,ref.shape)
        return best

    @staticmethod
    def value_mask(patch):
        if patch.size < 20:return None
        gray = cv2.resize(patch,(96,20)).astype('float32')
        detail = abs(gray-cv2.GaussianBlur(gray,(0,0),2.))
        contrast = float(np.percentile(detail,95))
        if contrast < 5:return None
        return detail > contrast*.35

    @staticmethod
    def difference(a,b):
        best = 1.
        for dx in (-2,0,2):
            shifted = np.roll(b,dx,axis=1)
            union = int(np.count_nonzero(a|shifted))
            if union:best = min(best,1-float(np.count_nonzero(a&shifted))/union)
        return best

    def detect(self, frame, full_size):
        result = dict(observed=False,graphics=False,display_changed=False,apply_box=None,
                      countdown=None,countdown_score=0.)
        fw,fh = full_size
        if frame is None or frame.shape[0] < fh*.94:return result
        factor = 1920/fw
        image = cv2.resize(frame[:,:,:3],None,fx=factor,fy=factor)
        gray = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);height,width = gray.shape
        result['observed'] = True
        # Graphical display controls have two distinct labels and their row order.
        roi = gray[round(height*.12):round(height*.36),round(width*.22):round(width*.58)]
        score,(x,y),scale,shape = self.match(roi,self.templates['resolution_cn'])
        if score >= .65:
            x += round(width*.22);y += round(height*.12)
            mode_roi = gray[y+round(35*scale):min(height,y+round(150*scale)),max(0,x-8):min(width,x+round(145*scale))]
            mode,_,_,_ = self.match(mode_roi,self.templates['display_mode_cn'],(scale,))
            if mode >= .65:
                result['graphics'] = True
                masks=[]
                for row in (0,40,80):
                    cy=y+shape[0]//2+round(row*scale)
                    patch=gray[max(0,cy-round(8*scale)):min(height,cy+round(9*scale)),
                               x+round(445*scale):min(width,x+round(650*scale))]
                    masks.append(self.value_mask(patch))
                if all(mask is not None for mask in masks):
                    if self.masks is None:self.masks=masks
                    elif max(self.difference(a,b) for a,b in zip(self.masks,masks)) >= .36:
                        if self.pending_masks is not None and max(self.difference(a,b) for a,b in zip(self.pending_masks,masks)) < .28:
                            result['display_changed']=True;self.masks=masks;self.pending_masks=None
                        else:self.pending_masks=masks
                    else:self.pending_masks=None
                save_roi=gray[round(height*.78):,round(width*.62):]
                save,(sx,sy),_,(sh,sw)=self.match(save_roi,self.templates['save_cn'])
                if save>=.65:
                    sx+=round(width*.62);sy+=round(height*.78)
                    result['apply_box']=[round(v/factor) for v in (sx-8,sy-8,sx+sw+8,sy+sh+8)]
        if not result['graphics']:
            self.masks=None;self.pending_masks=None
        # Menu extraction banners use a decimal timer below the settings panel.
        roi=gray[round(height*.70):round(height*.95),round(width*.25):round(width*.75)]
        score,(x,y),scale,(h,w)=self.match(roi,self.templates['extraction_countdown_cn'])
        result['countdown_score']=round(score,3)
        if score>=.70:
            value,confidence=self.numbers.read_number(roi[max(0,y-2):y+h+3,x+w+2:min(roi.shape[1],x+w+round(90*scale))],scale)
            if value is not None:
                result.update(countdown=value,digit_confidence=round(confidence,3))
        return result
