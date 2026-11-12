"""Extraction latch resolution: fresh evidence decides; time never hands the blackout to a death lane."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import numpy as np
from detector import Gate
import decision
from decision import DecisionEngine

ABSENT=dict(combat_score=0.,body_shape=0.,bar_frames=0.,label_shape=0.)
FADING=dict(combat_score=.32,body_shape=.07,bar_frames=.40,label_shape=.42)   # 2026-11-04 frame 15510
TOP=np.zeros((240,1920,3),dtype=np.uint8)


def latched(now=0.):
    g=Gate(delay_ms=300);g.max_observation_gap=.12;g.active=True;e=DecisionEngine(g)
    e.fade_pending=e.new_latch(now,'combat',dict(gain=.7,confirmed=True));e.last_frame=now;e.last_ui_context='combat'
    return e


def step(e,now,features):
    return e.step(now,(0,0),dict(features),0.,{},TOP,{},{})


class LatchTests(unittest.TestCase):
    def test_blackout_after_latch_is_extraction_even_past_one_second(self):
        e=latched()
        self.assertEqual(step(e,.3,FADING)['action'],'monitor')
        r=step(e,1.4,ABSENT)  # first black frame arrives 1.4 s after the latch (old code: cancelled -> death)
        self.assertEqual(r['action'],'extraction');self.assertEqual(r['evidence']['latch_frames'],2)

    def test_capture_stall_keeps_latch(self):
        e=latched();r=step(e,2.5,ABSENT)
        self.assertEqual(r['action'],'extraction')

    def test_unresolved_latch_ends_uncertain_never_death(self):
        # A fast stream exhausts the frame count but not the minimum time: keep waiting.
        e=latched();actions=[step(e,.03*i,FADING)['action'] for i in range(1,30)]
        self.assertEqual(set(actions),{'monitor'});self.assertIsNotNone(e.fade_pending)
        e=latched();actions=[step(e,.3*i,FADING)['action'] for i in range(1,decision.LATCH_FRAME_BUDGET+1)]
        self.assertNotIn('death',actions);self.assertEqual(actions[-1],'uncertain');self.assertIsNone(e.fade_pending)
        e=latched();r=step(e,decision.LATCH_WALL_CAP+.1,FADING)
        self.assertEqual(r['action'],'uncertain')

    def test_latch_blocks_gate_absence(self):
        e=latched();step(e,.3,FADING)
        self.assertEqual(e.gate.absence_observed,0.);self.assertEqual(e.gate.missing,0)

    def test_restarted_countdown_cancels_latch_and_death_lane_resumes(self):
        e=latched()
        cue=dict(banner=True,timer_state='nonzero',timer_source='center',center_value=7.)
        e.extraction.count_value=7.;e.extraction.count_last=.2
        r=e.step(.3,(0,0),dict(FADING),0.,{},TOP,{},cue)
        self.assertIsNone(e.fade_pending)
        self.assertIn('extraction_candidate_cancelled',[x['event'] for x in r['events']])

    def test_guards_are_one_source(self):
        e=latched()
        self.assertTrue(e.settlement_context()['protected'])
        self.assertTrue(e.isolation_context()['transition_protected'])
        self.assertEqual(e.isolation_context()['transition_basis'],'extraction_candidate')
        self.assertFalse(e.relative_context(.1,0)['allowed'])
        e.fade_pending=None;e.last_frame=.1
        self.assertFalse(e.settlement_context()['protected'])


if __name__=='__main__':unittest.main()
