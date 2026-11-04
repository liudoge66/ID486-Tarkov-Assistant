"""Fresh full-frame central extraction countdown, viewport-relative positioning."""
from pathlib import Path
import time
import cv2
import numpy as np
from transfer import TransferDetector

class CenterExtractionTimer:
    MAX_SECONDS=65
    def __init__(self,assets=None,numbers=None):
        root=Path(assets or Path(__file__).parent/'assets')
        self.label=cv2.imdecode(np.frombuffer((root/'settings/extraction_countdown_cn.png').read_bytes(),np.uint8),0)
        self.numbers=numbers or TransferDetector(root)
        self.icon=self.numbers.templates['running_icon']
        self.refs=[(scale,cv2.resize(self.label,None,fx=scale,fy=scale)) for scale in (.75,.85,1.,1.15,1.3)]
        self.reset()
    def reset(self):self.last_box=None;self.layout_key=None;self.long_reading=None
    def validate_long_reading(self,result,now):
        value=result.get('value')
        if value is None or value<=30:
            self.long_reading=None;return result
        prior=self.long_reading;self.long_reading=None
        strong=result.get('confidence',0)>=.88 and result.get('label_score',0)>=.82 and result.get('icon_score',0)>=.55
        matched=bool(strong and prior and .05<=now-prior[0]<=.75 and
                     -.2<=prior[1]-value<=now-prior[0]+.4)
        if strong:self.long_reading=(now,value)
        result=dict(result,raw_value=value,long_confirmed=matched)
        if not matched:result.update(value=None,reason='long_timer_needs_confirmation')
        return result
    def read_decimal(self,gray,scale):
        value,confidence=self.numbers.read_number(gray,scale)
        if value is not None:return value,confidence
        # At smaller resolutions, 8 and 6 can be close in template score.
        # Two visible enclosed counters supply independent shape evidence for
        # an already-leading, high-score 8; do not loosen all digit margins.
        if not gray.size:return None,0.
        _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)
        count,labels,stats,_=cv2.connectedComponentsWithStats(mask);chars=[];dots=[]
        for i in range(1,count):
            x,y,w,h,area=map(int,stats[i])
            if 9*scale<=h<=27*scale and 2*scale<=w<=19*scale and area>=9*scale*scale:
                glyph=np.where(labels[y:y+h,x:x+w]==i,255,0).astype('uint8')
                image=cv2.resize(glyph,(20,28)).astype('float32')/255.;by={}
                for label,reference,ratio in self.numbers.digits:
                    score=0. if abs(w/h-ratio)>.25 else .55*float(np.sum(image*reference)/(np.linalg.norm(image)*np.linalg.norm(reference)+1e-6))+.45*(1-float(np.mean(abs(image-reference))))
                    by[int(label)]=max(by.get(int(label),0.),score)
                ranked=sorted(by.items(),key=lambda a:a[1],reverse=True);margin=ranked[0][1]-ranked[1][1]
                contours,hierarchy=cv2.findContours(glyph,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
                holes=0 if hierarchy is None else sum(v[3]>=0 and cv2.contourArea(c)>=1.5*scale*scale for c,v in zip(contours,hierarchy[0]))
                resolved=(ranked[0][0]==8 and ranked[0][1]>=.90 and margin>=.01 and holes==2 and ranked[1][0] in (0,6,9))
                if ranked[0][1]<.80 or (margin<.025 and not resolved):return None,0.
                chars.append((x,ranked[0][0],ranked[0][1],y+h))
            elif 1*scale<=w<=5*scale and 1*scale<=h<=5*scale:dots.append((x,y+h))
        chars.sort()
        if not 2<=len(chars)<=4:return None,0.
        if not any(chars[-2][0]<x<chars[-1][0] and y>=min(c[3] for c in chars)-5*scale for x,y in dots):return None,0.
        value=int(''.join(str(c[1]) for c in chars))/10
        return (value,min(c[2] for c in chars)) if value<=self.MAX_SECONDS else (None,0.)
    def detect(self,frame,full_size,layout=None,observed_at=None):
        now=time.perf_counter() if observed_at is None else observed_at
        fw,fh=full_size
        if frame is None or frame.shape[:2]!=(fh,fw):return dict(observed=False,present=False,reason='needs_full_frame')
        layout=layout or dict(left=0,right=fw,top=0,width=fw,height=fh)
        left,right,top=layout['left'],layout['right'],layout['top'];aw=right-left;ah=fh-2*top
        key=(fw,fh,left,right,top)
        if key!=self.layout_key:self.reset();self.layout_key=key
        if aw<=0 or ah<=0:return dict(observed=False,present=False,reason='invalid_viewport')
        x0=left+round(aw*.20);x1=left+round(aw*.80);y0=top+round(ah*.68);y1=min(fh,top+round(ah*.96))
        crop=frame[y0:y1,x0:x1,:3]
        scale=min(aw/1920.,ah/1080.)
        mappings=[(1/scale,1/scale,'viewport_scale')]
        # Some stretched game modes scale fonts differently in x and y.
        if abs(aw/1920.-ah/1080.)/max(aw/1920.,ah/1080.)>.05:
            mappings.append((1920./aw,1080./ah,'stretched_viewport'))
        best=None;max_label=0.
        for fx,fy,mode in mappings:
            gray=cv2.cvtColor(cv2.resize(crop,None,fx=fx,fy=fy,interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY)
            for factor,ref in self.refs:
                if gray.shape[0]<ref.shape[0] or gray.shape[1]<ref.shape[1]:continue
                _,score,_,(x,y)=cv2.minMaxLoc(cv2.matchTemplate(gray,ref,cv2.TM_CCOEFF_NORMED));max_label=max(max_label,float(score))
                if score<.78:continue
                h,w=ref.shape
                # Independent running-man identity immediately left of the text.
                il=max(0,x-round(75*factor));ir=max(il,x-round(3*factor));iy=max(0,y-round(14*factor));iy2=min(gray.shape[0],y+h+round(14*factor))
                icon=cv2.resize(self.icon,None,fx=factor,fy=factor)
                icon_patch=gray[iy:iy2,il:ir]
                edge_score,_=self.numbers.edge(icon_patch,icon)
                gray_score=self.numbers.match(icon_patch,icon)
                if edge_score<.43 and gray_score<.78:continue
                icon_score=max(edge_score,gray_score)
                nl=x+w+round(2*factor);nr=min(gray.shape[1],nl+round(100*factor))
                nt=max(0,y-round(3*factor));nb=min(gray.shape[0],y+h+round(4*factor))
                value,confidence=self.read_decimal(gray[nt:nb,nl:nr],factor)
                combined=min(float(score),float(icon_score))
                box=[max(0,x0+round(il/fx)),max(0,y0+round(iy/fy)),min(fw,x0+round(nr/fx)),min(fh,y0+round(iy2/fy))]
                candidate=dict(observed=True,present=True,reason='center_extraction_widget',value=None if value is None or not 0<=value<=self.MAX_SECONDS else float(value),
                    confidence=float(confidence),label_score=round(float(score),4),icon_score=round(float(icon_score),4),
                    roi=[x0,y0,x1,y1],evidence_box=box,viewport=dict(left=left,right=right,top=top,width=fw,height=fh),mapping=mode,scale=factor)
                usable=candidate['value'] is not None and (candidate['value']<=30 or (candidate['confidence']>=.88 and candidate['label_score']>=.82 and candidate['icon_score']>=.55))
                rank=(usable,candidate['value'] is not None,round(combined,3),round(float(confidence),3))
                if best is None or rank>best['rank']:best=dict(candidate,score=combined,rank=rank,usable=usable)
            if best is not None and best.get('usable'):break
        if best is not None:
            best.pop('rank',None);best.pop('usable',None)
            self.last_box=best['evidence_box'];return self.validate_long_reading(best,now)
        self.long_reading=None
        return dict(observed=True,present=False,reason='center_widget_not_found',label_score=round(max_label,4),roi=[x0,y0,x1,y1],evidence_box=self.last_box)
