"""Cheap pixel evidence only. Darkness never authorizes an exit on its own."""
import cv2
import numpy as np
import threading,weakref,math
from collections import deque

_lock=threading.Lock()
_recent=deque(maxlen=2)


def black_scene(frame,full_size,layout):
    """Partitioned dark-scene evidence with sparse-residual/UI checks.

    Only the narrow performance-text and version-text corners are excluded.
    Current HUD anchors are checked separately before accepting absence.
    No exposure adjustment, temporal cache or brightness-only death decision.
    """
    # Share only immutable pixel preprocessing, not an observation or decision.
    # Weak owners prevent this two-entry memo from retaining full 4K images.
    owner=frame
    if frame is not None:
        while getattr(owner,'base',None) is not None and hasattr(owner.base,'flags'):owner=owner.base
    cacheable=frame is not None and not frame.flags.writeable and not owner.flags.writeable
    key=None
    if cacheable:
        key=(frame.ctypes.data,frame.shape,frame.strides,tuple(full_size),
             tuple(layout.get(k) for k in ('left','right','top','width','height')))
        with _lock:
            for ref,stored,result in _recent:
                if ref() is owner and stored==key:return dict(result,shared_preprocessing=True)
    result=_black_scene(frame,full_size,layout)
    if not result['certified'] and result.get('region') and result.get('maximum',0)>3:
        for x,y,x2,y2 in result['inspected_regions']:
            pixels=frame[y:y2,x:x2,:3]
            if pixels.size and int(np.max(pixels))==result['maximum']:
                iy,ix,_=np.unravel_index(int(np.argmax(pixels)),pixels.shape)
                result['maximum_xy']=[x+int(ix),y+int(iy)];break
    if cacheable:
        with _lock:_recent.append((weakref.ref(owner),key,dict(result)))
    return result


def _black_scene(frame,full_size,layout):
    fw,fh=full_size
    result=dict(certified=False,reason='incomplete_frame')
    if frame is None or frame.shape[0]<fh or frame.shape[1]<fw:return result
    if frame.dtype!=np.uint8 or frame.ndim!=3 or frame.shape[2] not in (3,4):
        return dict(certified=False,reason='unsupported_pixels')
    left,right,y=layout['left'],layout['right'],layout['top']
    if not (0<=left<right<=fw and 0<=y<fh//2):return dict(certified=False,reason='invalid_layout')
    # Menus can span beyond a pillarboxed viewport. Include the bars, top-right
    # controls and bottom-center Steam toolbar; exempt only the two text corners.
    step=2**max(0,math.ceil(math.log2(max(1.,fw/960,fh/540))))
    top=min(fh,math.ceil(max(12,round(fh*.018))/step)*step)
    bottom=max(top,(fh-max(12,round(fh*.025)))//step*step)
    if bottom<=top:return result
    perf_right=min(fw,math.ceil(fw*.65/step)*step)
    version_right=min(fw,math.ceil(fw*.25/step)*step)
    regions=[[0,top,fw,bottom],[perf_right,0,fw,top],[version_right,bottom,fw,fh]]
    maximum=max(float(np.max(frame[y:y2,x:x2,:3])) for x,y,x2,y2 in regions if x2>x and y2>y)
    scene=frame[:fh,:fw]
    result.update(reason='scene_not_dark',maximum=maximum,maximum_xy=None,region=[0,0,fw,fh],
                  inspected_regions=regions,ignored_regions=[[0,0,perf_right,top],[0,bottom,version_right,fh]])
    if maximum<=3:
        result.update(certified=True,reason='uniform_black_scene');return result

    scale=1/step;pw=(fw+step-1)//step;ph=(fh+step-1)//step
    pad=pw*step-fw;pad_y=ph*step-fh
    work=scene[:,:,:3] if not (pad or pad_y) else cv2.copyMakeBorder(scene[:,:,:3],0,pad_y,0,pad,cv2.BORDER_REPLICATE)
    preview=cv2.resize(work,(pw,ph),interpolation=cv2.INTER_AREA)
    preview[:top//step,:perf_right//step]=0
    preview[bottom//step:,:version_right//step]=0
    b,g,r=cv2.split(preview);peak=cv2.max(cv2.max(b,g),r)
    background=float(np.median(peak));p95=float(np.percentile(peak,95))
    result.update(background=round(background,3),p95=round(p95,3))
    if p95>12 or background>12:return result
    # Work in all color channels: a faint blue window must not be discarded by
    # luminance weighting. AREA pooling of a binary mask retains thin glyphs.
    cutoff=min(12,max(4,int(background)+4))
    visible=cv2.bitwise_not(cv2.inRange(scene[:,:,:3],(0,0,0),(cutoff-1,)*3))
    visible[:top,:perf_right]=0;visible[bottom:,:version_right]=0
    inspected_area=fh*fw-top*perf_right-(fh-bottom)*version_right
    fraction=float(cv2.countNonZero(visible))/max(1,inspected_area)
    occupancy=cv2.resize(visible if not (pad or pad_y) else cv2.copyMakeBorder(visible,0,pad_y,0,pad,cv2.BORDER_REPLICATE),(pw,ph),interpolation=cv2.INTER_AREA)
    result.update(foreground_cutoff=cutoff,foreground_fraction=round(fraction,7))
    if fraction>.005:
        result['reason']='foreground_too_broad';return result
    # A small bright patch must not be hidden by a large black remainder.
    largest=0.;box=None
    for gy in range(8):
        y0=ph*gy//8;y1=ph*(gy+1)//8
        for gx in range(12):
            x0=pw*gx//12;x1=pw*(gx+1)//12
            cell=occupancy[y0:y1,x0:x1]
            value=float(cv2.mean(cell)[0])/255 if cell.size else 0.
            if value>largest:
                largest=value;box=[round(x0*fw/pw),round(y0*fh/ph),round(x1*fw/pw),round(y1*fh/ph)]
    result.update(largest_cell_fraction=round(largest,5),largest_cell_box=box)
    if largest>.12:
        result['reason']='bright_region';return result
    ink=(occupancy>=4).astype('uint8')*255
    joined=cv2.morphologyEx(ink,cv2.MORPH_CLOSE,np.ones((2,5),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(joined,connectivity=8)
    unit=max(.5,fh*scale/540);parts=[]
    for x,y,w,h,area in stats[1:]:
        if area<max(3,round(3*unit*unit)):continue
        parts.append((int(x),int(y),int(w),int(h),int(area)))
        panel=(w>=12*unit and h>=10*unit and area>=45*unit*unit)
        text=((w>=20*unit and h>=2*unit) or
              (w>=8*unit and h>=3*unit and w>=1.25*h)) and area>=12*unit*unit
        if panel or text:
            result.update(reason='structured_foreground',structure='panel' if panel else 'text',
                structure_box=[round(x*fw/pw),round(y*fh/ph),round((x+w)*fw/pw),round((y+h)*fh/ph)])
            return result
    # Several glyphs/icons on one baseline are UI evidence even when separated.
    for a in parts:
        row=[b for b in parts if abs((b[1]+b[3]/2)-(a[1]+a[3]/2))<=3*unit]
        if len(row)>=3 and max(b[0]+b[2] for b in row)-min(b[0] for b in row)>=35*unit and sum(b[4] for b in row)>=20*unit*unit:
            result.update(reason='aligned_foreground',component_count=len(parts));return result
    result.update(certified=True,reason='near_black_sparse_residuals',component_count=len(parts))
    return result
