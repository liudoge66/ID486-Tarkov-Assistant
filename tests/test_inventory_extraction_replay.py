"""2026-11-18 15:10: inventory open while the extraction countdown ends. The whole screen fades
uniformly and turns black. Build 66 judged death (false exit); the inventory fade must open the
extraction latch instead. Frames come from the saved diagnostics folder."""
import copy,json,os,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import cv2,numpy as np
from detector import Gate,start_hud_evidence
import decision
from decision import DecisionEngine
from ui_fade import UniformFadeWatch

CASE=ROOT/'dist'/'数据'/'diagnostics'/'20261118_151047_364354+0800'
HUD=dict(combat_score=.8,body_shape=.8,bar_frames=.8,label_shape=.8,color=.8)


def load(name):
    return cv2.imdecode(np.fromfile(str(CASE/name),np.uint8),cv2.IMREAD_COLOR)


def menu_score(m):
    return max(m.get(k,0.) for k in ('inventory','settings','pause','confirmation'))


def run(engine_cls):
    data=json.loads((CASE/'frames.json').read_text(encoding='utf-8'))
    g=Gate(delay_ms=300);g.max_observation_gap=.35;g.arm((1,1),features=HUD);e=engine_cls(g)
    out=[]
    for s in data['scenes']:
        f=copy.deepcopy(s['features']);m=copy.deepcopy(s['menu_features'])
        top=load(s['files']['top_gray']);menu=load(s['files']['menu_gray']);world=cv2.cvtColor(load(s['files']['world']),cv2.COLOR_BGR2GRAY)
        scores=(f.get('label_shape',0.),f.get('combat_score',0.))
        r=e.step(s['frame_monotonic'],scores,f,menu_score(m),m,top,{},copy.deepcopy(s['extraction']),menu_top=menu,world=world)
        out.append((round(s['frame_monotonic']-data['scenes'][0]['frame_monotonic'],2),r['action'],[x['event'] for x in r['events']]))
        if r['action'] in ('death','extraction','uncertain'):break
    return out


@unittest.skipUnless((CASE/'frames.json').is_file(),'diagnostics case not on this machine')
class InventoryExtractionReplay(unittest.TestCase):
    def test_inventory_fade_at_countdown_end_is_extraction(self):
        out=run(DecisionEngine)
        actions=[a for _,a,_ in out];events=[x for *_,ev in out for x in ev]
        self.assertNotIn('death',actions,out)
        self.assertEqual(actions[-1],'extraction',out)
        self.assertIn('extraction_candidate_started',events)

    def test_stable_inventory_frames_never_confirm_a_fade(self):
        data=json.loads((CASE/'frames.json').read_text(encoding='utf-8'))
        w=UniformFadeWatch()
        for s in data['scenes'][:16]:    # countdown 4.1 -> 0.1, inventory steady
            r=w.update(s['frame_monotonic'],load(s['files']['menu_gray']),'inventory')
            self.assertFalse(r['confirmed'],r)


class InventoryFadeIsNotDeath(unittest.TestCase):
    """Without a countdown near its end the inventory is not watched; a confirmed inventory fade never kills."""
    TOP=np.zeros((240,1920,3),dtype=np.uint8)

    def engine(self):
        g=Gate(delay_ms=300);g.max_observation_gap=.12;g.arm((1,1),features=HUD);return DecisionEngine(g)

    def test_no_countdown_inventory_not_watched(self):
        e=self.engine();m=dict(inventory=.8)
        e.step(0.,(1,1),dict(HUD),0.,{},self.TOP,{},{})
        r=e.step(.1,(0,0),dict(combat_score=.05),.8,m,self.TOP,{},{},menu_top=self.TOP)
        self.assertNotEqual(r['early_esc_fade'].get('context'),'inventory')

    def test_confirmed_inventory_fade_without_latch_is_not_death(self):
        e=self.engine()
        e.esc_fade.update=lambda *a,**k:dict(confirmed=True,context='inventory',gain=.5)
        e.extraction.near_end=lambda now:False
        r=e.step(.1,(0,0),dict(combat_score=.05),.8,dict(inventory=.8),self.TOP,{},{},menu_top=self.TOP)
        self.assertNotEqual(r['action'],'death')


if __name__=='__main__':unittest.main()
