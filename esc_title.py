"""Local DB scene-text + title-layout detection. No input, capture or network APIs."""
from pathlib import Path
import time
import cv2
import numpy as np

class EscTitleDetector:
    def __init__(self,assets=None):
        self.path=Path(assets or Path(__file__).parent/'assets')/'scene_text/text_detection_en_ppocrv3_2023may.onnx'
        self.model=None

    def detect(self,frame,full_size,layout=None):
        started=time.perf_counter();fw,fh=full_size
        left,right,top=0,fw,0
        if layout:
            left,right,top=layout['left'],layout['right'],layout['top']
        active_w=right-left;active_h=fh-2*top
        # The word mark is left/right viewport aware, with room for tall/shifted titles.
        x0=left+round(active_w*.18);x1=left+round(active_w*.82)
        y0=top+round(active_h*.04);y1=top+round(active_h*.43)
        if frame.shape[0]<y1:return dict(present=False,reason='needs_expanded_capture',boxes=[],elapsed_ms=0.)
        crop=frame[y0:y1,x0:x1,:3]
        image=cv2.resize(crop,(960,352))
        if self.model is None:
            self.model=cv2.dnn_TextDetectionModel_DB(cv2.dnn.readNetFromONNX(np.frombuffer(self.path.read_bytes(),dtype=np.uint8)))
            self.model.setInputSize((960,352));self.model.setInputMean((123.675,116.28,103.53));self.model.setInputScale(1.0/255.0/np.array([.229,.224,.225]))
            self.model.setBinaryThreshold(.3);self.model.setPolygonThreshold(.5)
            self.model.setUnclipRatio(2.0);self.model.setMaxCandidates(80)
        boxes,scores=self.model.detect(image)
        candidates=[];all_boxes=[]
        for box,score in zip(boxes,scores):
            box=np.asarray(box);lo=box.min(axis=0);hi=box.max(axis=0)
            x,y=map(float,lo);x2,y2=map(float,hi);w=x2-x;h=y2-y
            all_boxes.append(dict(points=box.tolist(),score=round(float(score),4)))
            if score>=.65 and w>=65 and h>=16 and w>=h*1.4 and (y+y2)/2<190:
                candidates.append((x,y,x2,y2,float(score)))
        rows=[]
        for b in sorted(candidates,key=lambda b:(b[1]+b[3])/2):
            cy=(b[1]+b[3])/2
            row=next((r for r in rows if abs(cy-r['cy'])<=max(b[3]-b[1],r['height'])*.65),None)
            if row is None:rows.append(dict(boxes=[b],cy=cy,height=b[3]-b[1]))
            else:row['boxes'].append(b);row['cy']=sum((v[1]+v[3])/2 for v in row['boxes'])/len(row['boxes']);row['height']=max(v[3]-v[1] for v in row['boxes'])
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);edge=cv2.Canny(gray,35,90)
        evidence=None
        for row in rows:
            bs=row['boxes'];x=min(b[0] for b in bs);x2=max(b[2] for b in bs);y=min(b[1] for b in bs);y2=max(b[3] for b in bs)
            coverage=sum(b[2]-b[0] for b in bs)/(x2-x)
            # A broad upper title row, followed by a large graphic/letter region.
            width=(x2-x)/960;center=(x+x2)/1920
            lower=edge[min(351,round(y2+6)):min(352,round(y2+190)),max(0,round(x)):min(960,round(x2))]
            lower_density=float(np.mean(lower>0)) if lower.size else 0.
            if .38<=width<=.95 and .30<=center<=.70 and coverage>=.55 and 18<=row['height']<=155 and y<150 and lower_density>=.018:
                evidence=dict(row=[round(x),round(y),round(x2),round(y2)],row_coverage=round(coverage,4),lower_edge_density=round(lower_density,4),width_fraction=round(width,4))
                break
        return dict(present=evidence is not None,reason='title_text_and_layout' if evidence else 'no_title_layout',
                    boxes=all_boxes,evidence=evidence,roi=[x0,y0,x1,y1],elapsed_ms=round((time.perf_counter()-started)*1000,2))
