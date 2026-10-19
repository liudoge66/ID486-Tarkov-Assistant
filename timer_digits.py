"""Tiny fixed-font digit comparison, normalized by each glyph's own bounds."""
import cv2
import numpy as np

def red_mask(bgr):
    b,g,r=cv2.split(bgr)
    r=r.astype('int16');g=g.astype('int16');b=b.astype('int16')
    return ((r>45)&(r-g>18)&(r-b>18)).astype('uint8')*255

def glyphs(mask):
    found=[]
    # Bloom can join neighboring glyphs. Split at the vertical low-ink valley
    # near their shared boundary before finding each digit's own bounds.
    projection=np.sum(mask>0,axis=0)
    cut=min(range(20,29),key=lambda x:float(projection[x])+.1*abs(x-23.5))
    for left,right in ((6,cut),(cut,44)):
        piece=mask[:,left:right]
        count,labels,stats,_=cv2.connectedComponentsWithStats(piece)
        choices=[]
        for i in range(1,count):
            x,y,w,h,area=map(int,stats[i])
            if h>=7 and w>=3 and area>=14 and h<=30 and w<=25:choices.append((area,i,x,y,w,h))
        if not choices:return []
        _,i,x,y,w,h=max(choices)
        glyph=np.where(labels[y:y+h,x:x+w]==i,255,0).astype('uint8')
        normalized=cv2.resize(glyph,(20,28),interpolation=cv2.INTER_LINEAR).astype('float32')/255
        found.append((left+x,normalized,float(w/h)))
    return found

class TimerDigits:
    def __init__(self,path):
        bank=np.load(path)
        self.templates=[(int(bank['labels'][i]),bank['images'][i],float(bank['ratios'][i])) for i in range(len(bank['labels']))]

    def classify(self,bgr):
        chars=glyphs(red_mask(bgr))
        if len(chars)!=2:return dict(state='unknown',zero_score=0.,digits=[],scores=[],margins=[])
        digits=[];scores=[];margins=[];zero_scores=[]
        for _,image,ratio in chars:
            by_label={}
            for label,reference,ref_ratio in self.templates:
                if abs(ratio-ref_ratio)>.20:score=0.
                else:
                    cosine=float(np.sum(image*reference)/(np.linalg.norm(image)*np.linalg.norm(reference)+1e-6))
                    score=.55*cosine+.45*(1-float(np.mean(np.abs(image-reference))))
                by_label[label]=max(by_label.get(label,0.),score)
            ranked=sorted(by_label.items(),key=lambda pair:pair[1],reverse=True)
            label,score=ranked[0]
            digits.append(label);scores.append(score);margins.append(score-ranked[1][1]);zero_scores.append(by_label.get(0,0.))
        confident=all(score>=.90 and margin>=.035 for score,margin in zip(scores,margins))
        state=('zero' if digits==[0,0] else 'nonzero') if confident else 'unknown'
        return dict(state=state,zero_score=min(zero_scores),digits=digits,scores=scores,margins=margins)
