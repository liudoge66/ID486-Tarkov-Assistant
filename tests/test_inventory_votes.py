"""Build 73: inventory recognised by two of three cues (shift-tolerant tab text, tab boxes, Return)."""
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import cv2,numpy as np
import detector as D
DET=D.Detector()
SAMPLES=ROOT/'build_tools'/'samples_backup'/'diagnostics_20261125'


def strips(folder):
    F=SAMPLES/folder
    for s in json.loads((F/'frames.json').read_text(encoding='utf-8'))['scenes']:
        yield cv2.imdecode(np.fromfile(str(F/s['files']['menu_gray']),np.uint8),1)


def features(strip):
    DET.menu_score_top(strip);return DET.last_menu_features


@unittest.skipUnless((SAMPLES/'20261125_031540_233140+0800').is_dir(),'2026-11-25 samples not present')
class RealFrames(unittest.TestCase):
    def test_raid_timer_beside_return_still_inventory(self):
        # 03:15 false exit: Return 0.84 (red raid timer), tab text shifted; boxes + Return now agree.
        for strip in strips('20261125_031540_233140+0800'):
            f=features(strip)
            self.assertGreaterEqual(f['inventory'],.55);self.assertGreaterEqual(f['inventory_votes'],2)

    def test_quick_slot_bar_is_not_inventory(self):
        # 00:53:14 weapon switch: a highlighted quick slot looks like a tab box, but Return is absent.
        F=SAMPLES/'20261125_005317_064992+0800'
        scenes=json.loads((F/'frames.json').read_text(encoding='utf-8'))['scenes']
        found=False
        for s in scenes:
            strip=cv2.imdecode(np.fromfile(str(F/s['files']['menu_gray']),np.uint8),1);f=features(strip)
            if f['inventory_boxes'] and f['inventory_boxes']['present'] and f['inventory_return']<.3:
                found=True;self.assertEqual(f['inventory'],0.);self.assertLess(f['inventory_votes'],2)
        self.assertTrue(found,'quick-slot frame no longer produces a box; update this test')

    def test_no_non_inventory_frame_gets_two_votes(self):
        for F in sorted(SAMPLES.glob('2026*')):
            if F.name.startswith('20261125_0315'):continue
            for s in json.loads((F/'frames.json').read_text(encoding='utf-8'))['scenes']:
                if s['menu_features'].get('inventory_return',0)>=.6:continue
                f=features(cv2.imdecode(np.fromfile(str(F/s['files']['menu_gray']),np.uint8),1))
                self.assertEqual(f['inventory'],0.,F.name)


class SyntheticBoxes(unittest.TestCase):
    def row(self,selected=3,return_label=True):
        g=np.full((240,1920),30,np.uint8)
        for k in range(7):
            x=D.TAB_SLOT0+k*D.TAB_PITCH
            if k==selected:
                for y in range(5,40):g[y,x:x+140]=min(255,max(130,255-(y-8)*4))
            else:
                g[8:37,x+2:x+138]=50;cv2.putText(g,'TAB',(x+40,30),cv2.FONT_HERSHEY_SIMPLEX,.6,170,1)
        if return_label:
            ret=DET.menu_templates[1];g[4:4+ret.shape[0],1740:1740+ret.shape[1]]=ret
        return g

    def test_boxes_found_on_every_slot(self):
        for k in range(7):
            b=D.Detector.tab_boxes(self.row(k))
            self.assertTrue(b['present'],k);self.assertEqual(b['slot'],k)

    def test_boxes_plus_return_is_inventory(self):
        f=features(cv2.cvtColor(self.row(),cv2.COLOR_GRAY2BGR))
        self.assertGreaterEqual(f['inventory_return'],D.INVENTORY_RETURN_GATE);self.assertGreaterEqual(f['inventory'],.55)

    def test_boxes_alone_are_not_inventory(self):
        f=features(cv2.cvtColor(self.row(return_label=False),cv2.COLOR_GRAY2BGR))
        self.assertTrue(f['inventory_boxes']['present']);self.assertEqual(f['inventory'],0.)

    def test_two_highlights_are_not_a_tab_row(self):
        g=self.row(1);x=D.TAB_SLOT0+4*D.TAB_PITCH
        for y in range(5,40):g[y,x:x+140]=min(255,max(130,255-(y-8)*4))
        self.assertFalse(D.Detector.tab_boxes(g)['present'])


if __name__=='__main__':unittest.main()
