"""Locate the active top viewport without requiring a particular display aspect."""
import math
import cv2
import numpy as np

def capture_strip_height(width,height):
    # Width-scaled HUD plus possible top letterbox margin on taller displays.
    return min(height,math.ceil(min(width/8,height*240/1080)+max(0.,(height-width*9/16)/2)))

def _dark_pixels(image,threshold):
    # Capture pixels are unsigned bytes: max(B,G,R) <= t iff all three
    # channels lie in [0,t]. Keep the original arithmetic for other dtypes.
    if image.dtype==np.uint8:
        return cv2.inRange(image,(0,0,0),(threshold,threshold,threshold))>0
    return np.max(image,axis=2)<=threshold


def candidates(frame,full_size):
    height,width=frame.shape[:2];full_w,full_h=full_size
    if width!=full_w or full_w<=0 or full_h<=0:raise ValueError('Invalid capture dimensions')
    bgr=frame[:,:,:3];scale=width/1920
    start=min(height-1,max(1,round(35*scale)))
    kernel=max(3,round(width/240))|1
    if bgr.dtype==np.uint8 and height>start:
        rows=height-start;c995=math.ceil(rows*.995-1e-12);c97=math.ceil(rows*.97-1e-12)
        edge_w=min(width//3,kernel+2)
        l_edge=cv2.inRange(bgr[start:,:edge_w],(0,0,0),(12,12,12))
        r_edge=cv2.inRange(bgr[start:,width-edge_w:],(0,0,0),(12,12,12))
        if (not np.any(cv2.reduce(l_edge,0,cv2.REDUCE_SUM,dtype=cv2.CV_32S)[0]>=c97*255) and
            not np.any(cv2.reduce(r_edge,0,cv2.REDUCE_SUM,dtype=cv2.CV_32S)[0]>=c97*255)):
            left=robust_left=0;right=robust_right=width
        else:
            third=min(width//3+kernel+2,width);r_off=max(0,width-third)
            l_sub=bgr[start:,:third];r_sub=bgr[start:,r_off:]
            l_d=cv2.reduce(cv2.inRange(l_sub,(0,0,0),(3,3,3)),0,cv2.REDUCE_SUM,dtype=cv2.CV_32S)[0]>=c995*255
            r_d=cv2.reduce(cv2.inRange(r_sub,(0,0,0),(3,3,3)),0,cv2.REDUCE_SUM,dtype=cv2.CV_32S)[0]>=c995*255
            left=0;right=width
            while left<min(width//3,width-1) and l_d[left]:left+=1
            while right>max(width*2//3,left+1) and r_d[right-1-r_off]:right-=1
            if left<max(3,width*.005):left=0
            if width-right<max(3,width*.005):right=width
            l_nb=cv2.reduce(cv2.inRange(l_sub,(0,0,0),(12,12,12)),0,cv2.REDUCE_SUM,dtype=cv2.CV_32S)[0]>=c97*255
            r_nb=cv2.reduce(cv2.inRange(r_sub,(0,0,0),(12,12,12)),0,cv2.REDUCE_SUM,dtype=cv2.CV_32S)[0]>=c97*255
            l_nb=cv2.morphologyEx(l_nb.astype('uint8')[None,:],cv2.MORPH_CLOSE,np.ones((1,kernel),dtype='uint8'))[0].astype(bool)
            r_nb=cv2.morphologyEx(r_nb.astype('uint8')[None,:],cv2.MORPH_CLOSE,np.ones((1,kernel),dtype='uint8'))[0].astype(bool)
            robust_left=0;robust_right=width
            while robust_left<width//3 and l_nb[robust_left]:robust_left+=1
            while robust_right>width*2//3 and r_nb[robust_right-1-r_off]:robust_right-=1
            if robust_left<max(3,width*.005):robust_left=0
            if width-robust_right<max(3,width*.005):robust_right=width
        variants=[(0,width,0)]
        bounds=[(left,right),(0,width),(robust_left,robust_right)]
    else:
        dark=np.mean(_dark_pixels(bgr[start:],3),axis=0)>=.995
        left=0;right=width
        while left<min(width//3,width-1) and dark[left]:left+=1
        while right>max(width*2//3,left+1) and dark[right-1]:right-=1
        if left<max(3,width*.005):left=0
        if width-right<max(3,width*.005):right=width
        variants=[(0,width,0)]
        bounds=[(left,right),(0,width)]
        near_black=np.mean(_dark_pixels(bgr[start:],12),axis=0)>=.97
        near_black=cv2.morphologyEx(near_black.astype('uint8')[None,:],cv2.MORPH_CLOSE,
                                   np.ones((1,kernel),dtype='uint8'))[0].astype(bool)
        robust_left=0;robust_right=width
        while robust_left<width//3 and near_black[robust_left]:robust_left+=1
        while robust_right>width*2//3 and near_black[robust_right-1]:robust_right-=1
        if robust_left<max(3,width*.005):robust_left=0
        if width-robust_right<max(3,width*.005):robust_right=width
        bounds.append((robust_left,robust_right))
    # A contaminated border may be measurable on only one side. Try its mirror;
    # never assume a particular aspect or select it without matching actual HUD.
    for margin in (left,width-right,robust_left,width-robust_right):
        if width*.005<=margin<width/3:bounds.append((margin,width-margin))
    for x0,x1 in dict.fromkeys(bounds):
        if x1-x0<100:continue
        mid=bgr[:max(1,min(height,round(full_h*.15))),x0+int((x1-x0)*.38):x0+int((x1-x0)*.62)]
        rowdark=np.mean(_dark_pixels(mid,3),axis=1)>=.995
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
