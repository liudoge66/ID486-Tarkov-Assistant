import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from detector import ExtractionGuard
def cue(banner=False,zero=0,luma=100):return dict(banner=banner,zero_score=zero,scene_luma=luma)
g=ExtractionGuard()
for t in (0,.1,.2,.3):assert not g.update(t,.8,False,cue(True,1))
# Abrupt death while zero is visible or just disappears is NOT a success.
assert not g.update(.35,0,False,cue(True,1))
assert not g.update(.40,0,False,cue(False,luma=20))
g=ExtractionGuard()
for t in (0,.1,.2):g.update(t,.8,False,cue(True,1))
for t in (.3,.4,.5,.7,1.,1.6):assert not g.update(t,.8,False,cue()) # simply walking away
g=ExtractionGuard()
for t in (0,.1,.2):g.update(t,.8,False,cue(True,1))
assert not g.update(.3,.8,False,cue(luma=95))
assert not g.update(.4,.8,False,cue(luma=80))
assert not g.update(.51,.8,False,cue(luma=70))
assert g.update(.6,.8,False,cue(luma=60))
assert g.last_confirmation['current_luma']==60
g=ExtractionGuard()
for t in (0,.1,.2):g.update(t,.8,False,cue(True,1))
g.update(.3,.1,True,cue());assert not g.update(.5,.8,False,cue(luma=50))
print('PASS: countdown alone does not stop protection; abrupt death and walk-away not treated as success; observed fading sequence confirmed; menu resets stale evidence.')

# Verified zero may disappear before the green banner; do not forget it merely
# because glyphs fade. An unknown single glimpse must never create zero evidence.
g=ExtractionGuard()
for t in (0,.1,.2):g.update(t,.8,False,dict(**cue(True,1),timer_state='zero'))
for t in (.3,.4,.5):assert not g.update(t,.8,False,dict(**cue(True,.4),timer_state='unknown'))
assert not g.update(.6,.8,False,cue(luma=100))
assert not g.update(.9,.8,False,cue(luma=100))
assert not g.update(1.5,.8,False,cue(luma=95))
assert not g.update(1.7,.8,False,cue(luma=80))
assert g.update(1.9,.8,False,cue(luma=60))
g=ExtractionGuard();g.update(0,.8,False,dict(**cue(True,1),timer_state='zero'))
g.update(.1,.8,False,dict(**cue(True,0),timer_state='unknown'))
assert not g.update(.3,.8,False,cue(luma=20))
g=ExtractionGuard()
for t in (0,.1,.2):g.update(t,.8,False,dict(**cue(True,1),timer_state='zero'))
assert not g.update(.3,.8,False,dict(**cue(True,.99),timer_state='nonzero'))
assert not g.update(.5,.8,False,cue(luma=20))
print('PASS: confirmed-zero retention, delayed fade, unknown single-frame rejection, confident nonzero resets.')
