"""Locate the active top viewport without requiring a particular display aspect."""
import math
import cv2
import numpy as np

def capture_strip_height(width,height):
    # Width-scaled HUD plus possible top letterbox margin on taller displays.
    return min(height,math.ceil(min(width/8,height*240/1080)+max(0.,(height-width*9/16)/2)))

def candidates(frame,full_size):
    height,width=frame.shape[:2];full_w,full_h=full_size
    if width!=full_w or full_w<=0 or full_h<=0:raise ValueError('Invalid capture dimensions')
    bgr=frame[:,:,:3];scale=width/1920
    start=min(height-1,max(1,round(35*scale)))
    dark=np.mean(np.max(bgr[start:],axis=2)<=3,axis=0)>=.995
    left=0;right=width
    while left<min(width//3,width-1) and dark[left]:left+=1
    while right>max(width*2//3,left+1) and dark[right-1]:right-=1
    if left<max(3,width*.005):left=0
    if width-right<max(3,width*.005):right=width
    variants=[(0,width,0)]
    for x0,x1 in [(left,right),(0,width)]:
        if x1-x0<100:continue
        mid=bgr[:max(1,min(height,round(full_h*.15))),x0+int((x1-x0)*.38):x0+int((x1-x0)*.62)]
        rowdark=np.mean(np.max(mid,axis=2)<=3,axis=1)>=.995
        top=0
        while top<len(rowdark)-1 and rowdark[top]:top+=1
        if top<max(5,full_h*.008):top=0
        for y in [0,top]:
            v=(x0,x1,y)
            if v not in variants:variants.append(v)
    return [dict(left=x0,right=x1,top=y,width=full_w,height=full_h) for x0,x1,y in variants]

def project(frame,layout):
    left,right,y=layout['left'],layout['right'],layout['top'];width=right-left
    active_h=max(1,layout['height']-2*y)
    scale=min(width/1920,active_h/1080)
    needed=max(1,round(240*scale));patch=frame[y:min(frame.shape[0],y+needed),left:right,:3]
    if patch.shape[0]<needed:patch=cv2.copyMakeBorder(patch,0,needed-patch.shape[0],0,0,cv2.BORDER_CONSTANT)
    logical_w=max(1920,round(width/scale));base=cv2.resize(patch,(logical_w,240))
    combat=menu=extraction=base
    if logical_w>1920:
        combat=base[:,:1920].copy();menu=combat.copy();extraction=combat.copy()
        extra=logical_w-1920;half=extra//2
        # Left-anchored body, centered shortcuts, right-anchored close/extraction UI.
        combat[:,480:1600]=base[:,480+half:1600+half]
        combat[:,1600:1920]=base[:,1600+extra:1920+extra]
        menu[:,1600:1920]=base[:,1600+extra:1920+extra]
        extraction[:,1340:1920]=base[:,1340+extra:1920+extra]
    info=dict(**layout,active_width=width,active_height=active_h,hud_scale=round(scale,5),logical_width=logical_w)
    return combat,menu,extraction,info
