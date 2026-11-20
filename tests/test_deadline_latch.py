"""Countdown-deadline extraction latch (build 68).

A countdown read on >=3 consistent decrements gives an expected zero time. UI last seen no earlier
than 0.3 s before that zero, then gone, is extraction; UI gone earlier than that is still death.
Real-frame cases: 2026-11-19 16:27 (camera turning during the fade, falsely judged death by build 67)
and 2026-11-18 15:44 (grenade death with 4.6 s left, must stay death).
"""
import copy,json,os,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import numpy as np
from detector import Gate
import decision
from decision import DecisionEngine

HUD=dict(combat_score=.8,body_shape=.8,bar_frames=.8,label_shape=.8,color=.8)
GONE=dict(combat_score=0.,body_shape=0.,bar_frames=0.,label_shape=0.,color=0.)
TOP=np.zeros((240,1920,3),dtype=np.uint8)   # no structure: the fade watchers never confirm, only the deadline rule can latch


def engine():
    g=Gate(delay_ms=300);g.max_observation_gap=.35;g.arm((1,1),features=HUD);return DecisionEngine(g)


def cue(value):
    return dict(banner=True,timer_source='center',timer_state='nonzero' if value>0 else 'zero',center_value=value)


def step(e,t,features=HUD,extraction=None,menu=0.,menu_features=None):
    return e.step(t,(features['label_shape'],features['combat_score']),dict(features),menu,menu_features or {},TOP,{},extraction or {})


def countdown(e,start=7.,stop=.1,dt=.3):
    """Feed a steadily decreasing countdown; returns (time of last reading, expected zero)."""
    t=0.;v=start
    while v>=stop-1e-9:
        step(e,t,extraction=cue(round(v,1)));last=t;t=round(t+dt,3);v=round(v-dt,3)
    return last,e.deadline['at']


def finish(e,t,dt=.12,frames=40):
    actions=[];events=[]
    for i in range(frames):
        r=step(e,round(t+i*dt,3),GONE);actions.append(r['action']);events+=r['events']
        if r['action'] in ('death','extraction','uncertain'):break
    return actions,events


class DeadlineLatchTests(unittest.TestCase):
    def test_hud_kept_until_zero_then_gone_is_extraction(self):
        e=engine();last,zero=countdown(e)
        step(e,zero+.2)                                   # HUD still visible just after zero
        actions,events=finish(e,zero+.35)
        self.assertEqual(actions[-1],'extraction',actions)
        self.assertNotIn('death',actions)
        started=[x for x in events if x['event']=='extraction_candidate_started']
        self.assertEqual(started[0]['reason'],'countdown_deadline_ui_absent')

    def test_hud_lost_mid_countdown_is_death(self):
        e=engine();t=0.;v=7.
        for _ in range(10):step(e,t,extraction=cue(round(v,1)));t+=.3;v-=.3   # 4.0 s left
        actions,_=finish(e,t)
        self.assertEqual(actions[-1],'death',actions)

    def test_ui_last_seen_04_before_zero_is_death(self):
        e=engine();last,zero=countdown(e,stop=1.)
        step(e,zero-.4)                                   # last HUD frame 0.4 s before zero: outside 0.3
        actions,_=finish(e,zero-.28)
        self.assertEqual(actions[-1],'death',actions)

    def test_ui_last_seen_025_before_zero_is_extraction(self):
        e=engine();last,zero=countdown(e,stop=1.)
        step(e,zero-.25)                                  # inside the 0.3 s window
        actions,_=finish(e,zero-.13)
        self.assertEqual(actions[-1],'extraction',actions)

    def test_too_few_decrements_gives_no_deadline(self):
        e=engine()
        for i,v in enumerate((.6,.3)):step(e,i*.3,extraction=cue(v))
        self.assertIsNone(e.deadline)
        actions,_=finish(e,.6)
        self.assertEqual(actions[-1],'death',actions)

    def test_ui_returns_during_latch_cancels_without_exit(self):
        e=engine();last,zero=countdown(e)
        step(e,zero+.2)
        r=step(e,zero+.35,GONE)
        self.assertIsNotNone(e.fade_pending);self.assertEqual(e.fade_pending['context'],'deadline')
        r=step(e,zero+.47)                                # HUD back: still in the raid
        self.assertIsNone(e.fade_pending)
        self.assertIn('extraction_candidate_cancelled',[x['event'] for x in r['events']])
        self.assertIsNone(e.deadline)

    def test_transit_badge_misread_during_extraction_is_ignored(self):
        # 2026-11-19 16:27: the transit reader matched the extraction widget (value 2.8, 2.5 ...).
        e=engine();t=0.;v=7.
        while v>=.1-1e-9:
            badge=dict(countdown=True,kind='countdown',value=round(v,1),badge_observed=True) if v<=2.8 else {}
            e.step(t,(.8,.8),dict(HUD),0.,{},TOP,badge,cue(round(v,1)))
            t=round(t+.3,3);v=round(v-.3,3)
        self.assertFalse(e.transfer.active)
        zero=e.deadline['at'];step(e,zero+.2)
        actions,_=finish(e,zero+.35)
        self.assertEqual(actions[-1],'extraction',actions)

    def test_real_transit_countdown_without_extraction_widget_still_counts(self):
        e=engine();t=0.
        for v in (19.,18.7,18.4,18.1):
            e.step(t,(.8,.8),dict(HUD),0.,{},TOP,dict(countdown=True,kind='countdown',value=v,badge_observed=True),{});t+=.3
        self.assertTrue(e.transfer.active);self.assertTrue(e.transfer.recent_countdown(t-.3))

    def test_deadline_forgotten_long_after_zero(self):
        e=engine();last,zero=countdown(e)
        step(e,zero+decision.DEADLINE_AFTER+.1)
        self.assertIsNone(e.deadline)


REAL=ROOT/'dist'/'数据'/'diagnostics'


@unittest.skipUnless((REAL/'20261119_162750_980177+0800'/'frames.json').is_file(),'diagnostics not on this machine')
class RealFrameReplay(unittest.TestCase):
    def replay(self,name):
        sys.path.insert(0,str(ROOT/'build_tools'/'out68'))
        cwd=os.getcwd();os.chdir(ROOT)
        try:
            import trace_deadline
            return trace_deadline.replay(name,verbose=False)
        finally:os.chdir(cwd)

    def test_camera_turn_during_fade_is_extraction(self):
        self.assertEqual(self.replay('20261119_162750_980177+0800'),'extraction')

    def test_grenade_death_with_seconds_left_is_death(self):
        self.assertEqual(self.replay('20261118_154409_896479+0800'),'death')


if __name__=='__main__':unittest.main()
