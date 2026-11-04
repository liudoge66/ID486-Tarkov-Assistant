"""Fail-closed, copied visual-consumer contexts; no native or process APIs."""
import copy
import math
import numbers


def finite(value):
    if not isinstance(value,numbers.Real) or isinstance(value,bool):return False
    try:return math.isfinite(float(value))
    except (OverflowError,TypeError,ValueError):return False


def checked_layout(context,frame=None):
    if not isinstance(context,dict) or not isinstance(context.get('layout'),dict):
        return None,'missing_layout'
    raw=context['layout'];keys=('left','right','top','width','height')
    if any(not finite(raw.get(k)) or int(raw[k])!=raw[k] for k in keys):
        return None,'invalid_layout_values'
    layout={k:int(raw[k]) for k in keys}
    left,right,top,width,height=(layout[k] for k in keys)
    if not (width>0 and height>1 and 0<=left<right<=width and 0<=top<height/2):
        return None,'invalid_layout_bounds'
    if frame is not None:
        shape=getattr(frame,'shape',())
        if len(shape)!=3 or shape[2] not in (3,4) or shape[0]!=height or shape[1]!=width:
            return None,'incomplete_frame'
        if str(getattr(frame,'dtype',''))!='uint8':return None,'unsupported_pixels'
    return layout,None


def snapshot_context(context):
    if not isinstance(context,dict):return dict(active=False,invalid_reason='missing_context')
    try:result=copy.deepcopy(context)
    except Exception:return dict(active=False,invalid_reason='uncopyable_context')
    if not result.get('active'):return result
    layout,reason=checked_layout(result)
    region=result.get('full_region')
    if not reason and (not isinstance(region,(tuple,list)) or len(region)!=4 or
                       any(not finite(v) for v in region) or
                       tuple(region)!=(0,0,layout['width'],layout['height'])):
        reason='invalid_full_region'
    if not reason and (not finite(result.get('observed_at')) or
                       not isinstance(result.get('guard_epoch'),int)):
        reason='invalid_observation_metadata'
    if reason:return dict(active=False,invalid_reason=reason)
    result['layout']=layout;result['full_region']=tuple(region)
    return result
