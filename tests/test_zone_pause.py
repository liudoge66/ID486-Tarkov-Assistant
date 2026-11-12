"""Extraction/transit auto-pause ("监控自动暂停"): off by default, and when on no death lane runs in a zone."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import numpy as np
from detector import Gate
import decision
from decision import DecisionEngine

HUD=dict(combat_score=.8,body_shape=.8,bar_frames=.8,label_shape=.8)
GONE=dict(combat_score=0.,body_shape=0.,bar_frames=0.,label_shape=0.)
TOP=np.zeros((240,1920,3),dtype=np.uint8)


def engine(enabled=True):
    g=Gate(delay_ms=300);g.max_observation_gap=.12;g.arm((1,1),features=HUD);e=DecisionEngine(g);e.zone_enabled=enabled;return e


def cue(value):
    return dict(banner=True,timer_source='center',timer_state='nonzero' if value>0 else 'zero',center_value=value)


def step(e,t,features=HUD,extraction=None,transfer=None,menu=0.,suppress=False):
    return e.step(t,(0,0),dict(features),menu,{},TOP,transfer or {},extraction or {},suppress_fade=suppress)


def enter(e,t0=0.):
    step(e,t0,extraction=cue(7.));r=step(e,t0+.3,extraction=cue(6.7))
    return r


class ZonePauseTests(unittest.TestCase):
    def test_enter_on_live_countdown(self):
        e=engine();r=enter(e)
        self.assertEqual(r['action'],'zone_pause');self.assertEqual(r['zone'],'extraction')
        self.assertIn('zone_entered',[x['event'] for x in r['events']])
        self.assertTrue(e.settlement_context()['protected']);self.assertFalse(e.relative_context(.3,0)['allowed'])
        self.assertEqual(e.isolation_context()['transition_basis'],'zone_extraction')

    def test_hud_gone_in_zone_is_standby_never_death(self):
        e=engine();enter(e)
        actions=[step(e,.6+.1*i,GONE)['action'] for i in range(4)]
        self.assertNotIn('death',actions);self.assertIn('zone_done',actions);self.assertIsNone(e.zone);self.assertFalse(e.gate.active)

    def test_walk_out_resumes_after_stale_countdown_and_full_hud(self):
        e=engine();enter(e)
        self.assertEqual(step(e,1.2)['action'],'zone_pause')      # countdown stale ~0.9s, not yet
        self.assertEqual(step(e,1.4)['action'],'zone_pause')      # stale>=1s: HUD timer starts
        r=step(e,2.45)
        self.assertEqual(r['action'],'monitor');self.assertIn('zone_left',[x['event'] for x in r['events']]);self.assertIsNone(e.zone)
        # Back to normal: HUD absence is death again.
        actions=[step(e,2.6+.15*i,GONE)['action'] for i in range(6)]
        self.assertIn('death',actions)

    def test_reentering_countdown_pauses_again(self):
        e=engine();enter(e);step(e,1.4);step(e,2.45)
        r=enter(e,3.)
        self.assertEqual(r['action'],'zone_pause')

    def test_menu_inside_zone_keeps_pause(self):
        e=engine();enter(e)
        for i in range(60):
            r=step(e,.6+.25*i,GONE,menu=.9)
            self.assertEqual(r['action'],'zone_pause',i)
        self.assertIsNotNone(e.zone)

    def test_transit_long_countdown_pauses(self):
        e=engine()
        step(e,0.,transfer=dict(countdown=True,kind='countdown',value=19.,badge_observed=True))
        r=step(e,.3,transfer=dict(countdown=True,kind='countdown',value=18.7,badge_observed=True))
        self.assertEqual(r['action'],'zone_pause');self.assertEqual(r['zone'],'transfer')

    def test_short_confirm_does_not_pause(self):
        e=engine()
        step(e,0.,transfer=dict(confirm=True,kind='confirm',value=1.8,badge_observed=True))
        r=step(e,.3,transfer=dict(confirm=True,kind='confirm',value=1.5,badge_observed=True))
        self.assertNotEqual(r['action'],'zone_pause');self.assertIsNone(e.zone)

    def test_focus_loss_in_zone_needs_longer_hud_proof(self):
        e=engine();enter(e);e.pause()
        self.assertIsNotNone(e.zone)
        step(e,1.4);self.assertEqual(step(e,2.5)['action'],'zone_pause')
        self.assertEqual(step(e,4.5)['action'],'monitor')

    def test_limbo_ends_in_standby(self):
        e=engine();enter(e);half=dict(combat_score=.32,body_shape=.07,bar_frames=.40,label_shape=.42)
        actions=[step(e,1.4+.25*i,half)['action'] for i in range(60)]
        self.assertNotIn('death',actions);self.assertIn('zone_done',actions)

    def test_switch_off_keeps_existing_flow(self):
        e=engine(False);enter(e)
        self.assertIsNone(e.zone)
        actions=[step(e,.6+.15*i,GONE)['action'] for i in range(6)]
        self.assertIn('death',actions)

    def test_switch_turned_off_mid_zone_resumes_normal(self):
        e=engine();enter(e);e.zone_enabled=False
        r=step(e,.5);self.assertIsNone(e.zone);self.assertEqual(r['action'],'monitor')


if __name__=='__main__':unittest.main()
