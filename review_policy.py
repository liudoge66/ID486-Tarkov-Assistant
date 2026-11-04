"""Pure policy for retaining an already available complete observation."""
def full_frame_can_continue(shape,full_size,*,safe,epoch_match,owner_ok,transition=False,protected=False):
    width,height=full_size
    return bool(tuple(shape[:2])==(height,width) and safe and epoch_match and owner_ok and
                not transition and not protected)

def scope_expansion_can_preserve(shape,full_size,*,safe,epoch_match,owner_ok,transition=False,protected=False):
    """An ordinary top-to-full request is not a display/owner transition."""
    width,height=full_size
    return bool(len(shape)>=2 and shape[1]==width and 0<shape[0]<height and
                safe and epoch_match and owner_ok and not transition and not protected)
