"""Viewport-relative scene preview for network candidates only."""
import cv2
from probe_context import checked_layout

def preview(frame,layout):
    if frame is None:return None
    layout,reason=checked_layout(dict(layout=layout),frame)
    if reason:return None
    left,right,top=layout['left'],layout['right'],layout['top']
    width=right-left;height=layout['height']-2*top
    x0=left+round(width*.08);x1=left+round(width*.92)
    y0=top+round(height*.30);y1=top+round(height*.86)
    crop=frame[y0:y1,x0:x1,:3]
    if not crop.size:return None
    return cv2.cvtColor(cv2.resize(crop,(192,96),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY)
