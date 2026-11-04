"""Local synthetic regressions. No game capture/input, WFP, audio or real processes."""
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app_storage import load_settings,writable_data_directory
from detector import Gate,Detector
from isolation_candidate import IsolationCandidate
import numpy as np


class ReleaseTests(unittest.TestCase):
    def test_settings_types_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'settings.json'
            self.assertEqual(load_settings(p),{})
            for value in ([],None,42,'text',False):
                p.write_text(json.dumps(value),encoding='utf-8')
                self.assertEqual(load_settings(p),{})
            p.write_text('{bad',encoding='utf-8');self.assertEqual(load_settings(p),{})
            p.write_text('{"delay_ms":300}',encoding='utf-8-sig')
            self.assertEqual(load_settings(p),{'delay_ms':300})

    def test_data_directory_fallback(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOCALAPPDATA':tmp}):
            blocked=Path(tmp)/'file';blocked.write_text('not a directory')
            dest=writable_data_directory(blocked/'data')
            self.assertEqual(dest,Path(tmp)/'TarkovHUDMonitor'/'V0.3')
            preferred=Path(tmp)/'preferred'
            self.assertEqual(writable_data_directory(preferred),preferred)

    def test_continuous_absence_and_recovery(self):
        visible=dict(combat_score=.8,body_shape=.8,bar_frames=.8,label_shape=.8)
        zero={k:0. for k in visible}
        g=Gate(delay_ms=300);g.max_observation_gap=.12
        self.assertTrue(g.arm((1,1),features=visible))
        for t in (0.,.3,.6):self.assertFalse(g.update((0,0),now=t,combat_score=0,features=zero))
        self.assertTrue(g.update((0,0),now=.9,combat_score=0,features=zero))
        self.assertFalse(g.update((0,0),now=1.,combat_score=0,features=zero))
        g.arm((1,1),features=visible)
        g.update((0,0),now=2.,combat_score=0,features=zero)
        self.assertFalse(g.update((1,1),now=2.1,combat_score=.8,features=visible))
        self.assertIsNone(g.missing_since)

    def test_hud_collapse_network_candidate_guards(self):
        def feed(c,t,i,mean=100.,**kw):
            return c.update(t,i,kw.pop('epoch',0),valid=kw.pop('valid',True),absent=kw.pop('absent',True),protected=kw.pop('protected',False),photometry=dict(mean=mean,tiles=[mean]*6),motion=kw.pop('motion',dict(changed=True,error=.5)),hud_collapsed=kw.pop('hud_collapsed',True))
        for kind in ('positive','hud','protected','epoch','duplicate','gap','motion','error','static','collapse'):
            with self.subTest(kind=kind):
                c=IsolationCandidate();feed(c,1.,1);feed(c,1.1,2,99.)
                kw={};t=1.2;i=3;mean=98.
                if kind=='hud':kw['absent']=False
                if kind=='protected':kw['protected']=True
                if kind=='epoch':kw['epoch']=1
                if kind=='duplicate':i=2
                if kind=='gap':t=2.
                if kind=='motion':kw['motion']=dict(changed=False,error=.5)
                if kind=='error':kw['motion']=dict(changed=True,error=.44)
                if kind=='static':mean=100.
                if kind=='collapse':kw['hud_collapsed']=False
                self.assertEqual(feed(c,t,i,mean,**kw)['action']=='isolate',kind=='positive')

    def test_hud_score_cache(self):
        frame=np.random.default_rng(52).integers(0,256,(240,1920,3),dtype=np.uint8)
        d=Detector()
        for image in (frame,frame.copy(),frame//2,frame.copy()):
            scores=d.scores_top(image);features=dict(d.last_features)
            baseline=Detector()
            self.assertEqual(scores,baseline.scores_top(image))
            self.assertEqual(features,baseline.last_features)

    def test_live_only_source_and_syntax(self):
        for p in ROOT.glob('*.py'):ast.parse(p.read_text(encoding='utf-8-sig'))
        source=(ROOT/'monitor.py').read_text(encoding='utf-8')
        self.assertNotIn('--video-test',source)
        self.assertNotIn('open_video_test',source)
        self.assertNotIn('open_video_test',(ROOT/'primary_ui.py').read_text(encoding='utf-8'))
        self.assertNotIn('av==',(ROOT/'requirements.txt').read_text())


if __name__=='__main__':unittest.main()
