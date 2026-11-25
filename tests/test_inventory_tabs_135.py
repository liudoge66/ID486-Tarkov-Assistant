"""EFT 1.3.5 inventory tab row (build 72).

2026-11-25 00:53 / 01:02: the tab row on the skills page was drawn ~1 px off and scored 0.51 against the
0.60 gate, so the open inventory looked like "HUD and menu gone" and was judged as death. A shift-tolerant
match that ignores the weakest segment is used only when the Return label is clearly present.
"""
import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import cv2,numpy as np
import detector as D

SAMPLES=ROOT/'build_tools'/'samples_backup'/'diagnostics_20261125'
DET=D.Detector()


def strip_from(folder,scene=-1):
    s=json.loads((folder/'frames.json').read_text(encoding='utf-8'))['scenes'][scene]
    return cv2.imdecode(np.fromfile(str(folder/s['files']['menu_gray']),np.uint8),cv2.IMREAD_COLOR)


def shifted(strip,dx):
    return cv2.warpAffine(strip,np.float32([[1,0,dx],[0,1,0]]),(strip.shape[1],strip.shape[0]),borderMode=cv2.BORDER_REPLICATE)


class SyntheticTabRow(unittest.TestCase):
    def strip(self):
        """Draw the stored tab and Return templates onto a dark strip, like an open inventory."""
        s=np.full((240,1920),20,np.uint8)
        tabs,ret=DET.menu_templates
        s[14:40,280:850]=tabs;s[4:4+ret.shape[0],1740:1740+ret.shape[1]]=ret
        return cv2.cvtColor(s,cv2.COLOR_GRAY2BGR)

    def test_aligned_row_uses_strict_path(self):
        DET.menu_score_top(self.strip());f=DET.last_menu_features
        self.assertGreaterEqual(f['inventory'],.55);self.assertIsNone(f['inventory_nav_tolerant'])

    def test_three_pixel_shift_is_recovered_only_with_return(self):
        s=shifted(self.strip(),3)
        s[:,1700:]=self.strip()[:,1700:]        # Return label stays in place
        DET.menu_score_top(s);f=DET.last_menu_features
        self.assertLess(f['inventory_nav'],.60)
        self.assertGreaterEqual(f['inventory'],.55)
        s[0:40,1700:]=20                         # same shifted row without Return
        DET.menu_score_top(s);f=DET.last_menu_features
        self.assertEqual(f['inventory'],0.);self.assertIsNone(f['inventory_nav_tolerant'])

    def test_return_gate_constant(self):
        # build 73 lowered the gate from .85 to .75 (red raid timer beside Return scored .84); saved
        # non-inventory frames peak at .61, so keep a clear margin above that.
        self.assertGreaterEqual(D.INVENTORY_RETURN_GATE,.70)


@unittest.skipUnless((SAMPLES/'20261125_010230_472241+0800'/'frames.json').is_file(),'1.3.5 samples not on this machine')
class RealFrames(unittest.TestCase):
    def test_false_exit_skills_page_is_inventory(self):
        for name in ('20261125_005317_064992+0800','20261125_010230_472241+0800'):
            DET.menu_score_top(strip_from(SAMPLES/name));f=DET.last_menu_features
            self.assertLess(f['inventory_nav'],.60,name)        # strict path still fails ...
            self.assertGreaterEqual(f['inventory'],.55,name)    # ... the tolerant path recognises it

    def test_non_inventory_frames_unchanged(self):
        checked=0
        for folder in sorted(SAMPLES.glob('2026*')):
            scenes=json.loads((folder/'frames.json').read_text(encoding='utf-8'))['scenes']
            for i,s in enumerate(scenes):
                if s['menu_features'].get('inventory_return',0)>=.6:continue
                DET.menu_score_top(strip_from(folder,i));f=DET.last_menu_features
                self.assertLess(f['inventory'],.55,(folder.name,i));self.assertLess(f['inventory_votes'],2,(folder.name,i));checked+=1
        self.assertGreater(checked,300)

    def test_user_screenshots_all_pages(self):
        pngs=sorted((SAMPLES/'user_screenshots_135'/'png_fullres').glob('*.png'))
        self.assertGreaterEqual(len(pngs),9)
        for p in pngs:
            im=cv2.imdecode(np.fromfile(str(p),np.uint8),cv2.IMREAD_COLOR)
            DET.menu_score_top(cv2.resize(im,(1920,1080),interpolation=cv2.INTER_AREA)[:240])
            self.assertGreaterEqual(DET.last_menu_features['inventory'],.55,p.name)


if __name__=='__main__':unittest.main()
