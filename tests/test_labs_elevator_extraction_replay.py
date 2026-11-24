"""2026-11-22 20:42 Labs elevator extraction judged death (false exit, V0.31).

The countdown digits "4.1" sat over the player's bright sleeve with the dark glove
next to them, so the Otsu digit reader never produced a number, extraction was
never near_end, and HUD loss confirmed death. The saved full frames are re-read
with the current CenterExtractionTimer and merged into the saved cues the same
way detector.merge_center_timer does, then replayed through DecisionEngine.
"""
import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import cv2,numpy as np
from detector import Gate
from decision import DecisionEngine
from center_extraction import CenterExtractionTimer

CASE=ROOT/'dist'/'数据'/'diagnostics'/'20261122_204228_256698+0800'
HUD=dict(combat_score=.8,body_shape=.8,bar_frames=.8,label_shape=.8,color=.8)


def load(name,flags=cv2.IMREAD_COLOR):
    return cv2.imdecode(np.fromfile(str(CASE/name),np.uint8),flags)


def full_frames(data):
    return [(f['monotonic'],f['file']) for f in data['frames']]


def reread(data):
    """Countdown value per scene from the nearest saved full frame (<=60 ms apart)."""
    timer=CenterExtractionTimer();frames=full_frames(data);out={}
    for s in data['scenes']:
        t,name=min(frames,key=lambda f:abs(f[0]-s['frame_monotonic']))
        if abs(t-s['frame_monotonic'])>.06:continue
        out[s['frame_sequence']]=timer.detect(load(name),(3840,2160),observed_at=s['frame_monotonic'])
    return out


def merged_cue(saved,center):
    cue=copy.deepcopy(saved)
    if center is None or not center.get('present'):return cue
    value=center.get('value')
    cue.update(banner=True,timer_source='center',center_timer=center,center_value=value,
               timer_state='unknown' if value is None else 'zero' if value==0 else 'nonzero',
               timer_digits=[],timer_text=None if value is None else f'{value:.1f}',
               zero_score=center.get('confidence',0.) if value==0 else 0.)
    return cue


def menu_score(m):
    return max(float(m.get(k,0.) or 0.) for k in ('inventory','settings','pause','confirmation'))


def run(use_reread=True):
    data=json.loads((CASE/'frames.json').read_text(encoding='utf-8'))
    centers=reread(data) if use_reread else {}
    g=Gate(delay_ms=300);g.max_observation_gap=.35;g.arm((1,1),features=HUD);e=DecisionEngine(g)
    out=[]
    for s in data['scenes']:
        f=copy.deepcopy(s['features']);m=copy.deepcopy(s['menu_features'])
        top=load(s['files']['top_gray']);menu=load(s['files']['menu_gray'])
        world=cv2.cvtColor(load(s['files']['world']),cv2.COLOR_BGR2GRAY)
        cue=merged_cue(s['extraction'],centers.get(s['frame_sequence']))
        r=e.step(s['frame_monotonic'],(f.get('label_shape',0.),f.get('combat_score',0.)),f,menu_score(m),m,top,
                 m.get('transfer',{}).get('cues',{}),cue,menu_top=menu,world=world)
        out.append((s['frame_sequence'],cue.get('center_value'),r['action'],[x['event'] for x in r['events']]))
        if r['action'] in ('death','extraction','uncertain'):break
    return out


@unittest.skipUnless((CASE/'frames.json').is_file(),'diagnostics case not on this machine')
class LabsElevatorExtraction(unittest.TestCase):
    def test_countdown_is_read_and_decreases(self):
        data=json.loads((CASE/'frames.json').read_text(encoding='utf-8'))
        values=[c.get('value') for _,c in sorted(reread(data).items()) if c.get('present')]
        read=[v for v in values if v is not None]
        self.assertGreaterEqual(len(read),10,values)
        self.assertEqual(read,sorted(read,reverse=True),values)
        self.assertLessEqual(read[-1],1.0,values)

    def test_replay_is_extraction_not_death(self):
        out=run()
        actions=[a for _,_,a,_ in out]
        self.assertNotIn('death',actions,out)
        self.assertEqual(actions[-1],'extraction',out)

    def test_saved_cues_alone_still_reproduce_the_old_death(self):
        # Guards the test itself: without the new readings the saved cues give death.
        self.assertEqual(run(use_reread=False)[-1][2],'death')


if __name__=='__main__':
    unittest.main()
