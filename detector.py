"""Visual-only HUD detector. No keyboard or process APIs."""
from pathlib import Path
import time
import cv2
import numpy as np
from timer_digits import TimerDigits
cv2.setNumThreads(1)

class Detector:
    def __init__(self, asset_dir=None):
        root = Path(asset_dir or Path(__file__).parent / 'assets')
        self.masks = [cv2.imread(str(root / n), 0) for n in ('health.png', 'bar.png')]
        if any(m is None or np.count_nonzero(m) < 20 for m in self.masks):
            raise ValueError('HUD reference masks missing or invalid')
        self.menu_templates=[cv2.imread(str(root/n),0) for n in ('menu_tabs.png','menu_return.png')]
        if any(t is None for t in self.menu_templates):raise ValueError('Menu references missing')
        self.inventory_edges=cv2.Canny(self.menu_templates[0],35,90)
        self.inventory_return_edges=cv2.Canny(self.menu_templates[1],35,90)
        self.extra_menu_templates={name:cv2.imread(str(root/(name+'.png')),0) for name in
            ('settings_title','settings_return','pause_logo_left','pause_logo_right','confirm_title','confirm_subtitle')}
        if any(t is None for t in self.extra_menu_templates.values()):raise ValueError('Additional menu references missing')
        self.settings_return_edges=cv2.Canny(self.extra_menu_templates['settings_return'],35,90)
        self.last_menu_features={}
        self.extract_title=cv2.imread(str(root/'extract_title.png'),0)
        self.extract_zero=cv2.imread(str(root/'extract_zero.png'),0)
        if self.extract_title is None or self.extract_zero is None:raise ValueError('Extraction references missing')
        self.last_extraction={}
        self.timer_digits=TimerDigits(root/'timer_digits.npz')
        shape=np.load(root/'body_shape.npz')
        self.body_mask=shape['mask'];self.body_bins=shape['bins']
        self.bar_h=cv2.imread(str(root/'bar_horizontal.png'),0)
        self.bar_v=cv2.imread(str(root/'bar_vertical.png'),0)
        self.labels=cv2.imread(str(root/'label_shape.png'),0)
        if any(x is None for x in (self.bar_h,self.bar_v,self.labels)):raise ValueError('Shape references missing')
        self.last_features={}

    def normalize_top(self, frame, full_size=None):
        h,w=frame.shape[:2]
        full_w,full_h=full_size or (w,h)
        if w!=full_w or abs(full_w/full_h-16/9)>.025:
            raise ValueError('Only 16:9 game area supported by this HUD profile')
        strip_h=round(full_h*240/1080)
        if h<strip_h:raise ValueError('Capture strip is too short')
        top=cv2.resize(frame[:strip_h],(1920,240))
        if top.shape[2]==4:
            top=cv2.cvtColor(top,cv2.COLOR_BGRA2BGR)
        return top

    def analyze(self, frame, full_size=None):
        top=self.normalize_top(frame,full_size)
        self.last_top=top  # owned resize output; safe for a bounded diagnostic ring
        self.last_extraction=self.extraction_features(top)
        scores=self.scores_top(top)
        # A confident combat HUD already prevents absence. Avoid expensive logo
        # searches in this common case; inspect menus only when HUD is unclear.
        if self.last_features['combat_score']>=.48:
            self.last_menu_features=dict(inventory=0.,settings=0.,pause=0.,confirmation=0.,skipped_for_combat=True)
            return scores,0.
        return scores,self.menu_score_top(top)

    def extraction_features(self,top):
        scene_luma=float(cv2.cvtColor(top[100:230,200:1600],cv2.COLOR_BGR2GRAY).mean())
        hsv=cv2.cvtColor(top[3:58,1345:1775],cv2.COLOR_BGR2HSV)
        green=float(np.mean((hsv[:,:,0]>20)&(hsv[:,:,0]<50)&(hsv[:,:,1]>100)&(hsv[:,:,2]>100)))
        title=zero=raw_zero=0.
        timer=dict(state='unknown',digits=[],scores=[],margins=[])
        if green>.55:
            gray=cv2.cvtColor(top[6:51,1348:1652],cv2.COLOR_BGR2GRAY)
            title=float(cv2.minMaxLoc(cv2.matchTemplate(gray,self.extract_title,cv2.TM_CCOEFF_NORMED))[1])
            hsv=cv2.cvtColor(top[73:99,1856:1891],cv2.COLOR_BGR2HSV)
            red=(((hsv[:,:,0]<12)|(hsv[:,:,0]>170))&(hsv[:,:,1]>100)&(hsv[:,:,2]>80)).astype('uint8')*255
            if np.any(red):raw_zero=float(cv2.minMaxLoc(cv2.matchTemplate(red,self.extract_zero,cv2.TM_CCORR_NORMED))[1])
            timer=self.timer_digits.classify(top[69:103,1850:1897])
            zero=timer['zero_score']
        return dict(banner=green>.55 and title>=.65,green_fraction=green,title_score=title,zero_score=zero,raw_zero_score=raw_zero,
            timer_state=timer['state'],timer_digits=timer['digits'],timer_scores=timer['scores'],timer_margins=timer['margins'],scene_luma=scene_luma)

    def menu_score(self, frame):
        return self.menu_score_top(self.normalize_top(frame))

    def menu_score_top(self, top):
        # All anchors stay inside the existing top strip. Ignore selected-tab
        # grayscale polarity by matching inventory text edges instead.
        gray=cv2.cvtColor(top,cv2.COLOR_BGR2GRAY)
        def correlation(box,template,margin_x=2,margin_y=2):
            x1,y1,x2,y2=box
            region=gray[max(0,y1-margin_y):min(gray.shape[0],y2+margin_y),max(0,x1-margin_x):x2+margin_x]
            return max(0.,float(cv2.minMaxLoc(cv2.matchTemplate(region,template,cv2.TM_CCOEFF_NORMED))[1]))
        inv_edge=self.distance_support(cv2.Canny(gray[14:40,280:850],35,90),self.inventory_edges)
        inv_return=self.distance_support(cv2.Canny(gray[4:36,1740:1840],35,90),self.inventory_return_edges)
        # The right-hand Return label can disappear against bright scenery.
        # Navigation remains the required evidence; Return is only supporting.
        inventory=.90*inv_edge+.10*inv_return if inv_edge>=.60 else 0.
        settings_title=correlation((840,5,1010,65),self.extra_menu_templates['settings_title'])
        settings_return=self.distance_support(cv2.Canny(gray[25:78,1765:1845],35,90),self.settings_return_edges)
        settings=min(settings_title,settings_return)
        pause_left=correlation((530,125,1030,180),self.extra_menu_templates['pause_logo_left'],48,35)
        pause_right=correlation((1040,125,1290,180),self.extra_menu_templates['pause_logo_right'],48,35)
        pause=min(pause_left,pause_right)
        confirm_title=correlation((875,18,1045,58),self.extra_menu_templates['confirm_title'])
        confirm_subtitle=correlation((905,58,1020,80),self.extra_menu_templates['confirm_subtitle'])
        confirmation=min(confirm_title,confirm_subtitle)
        self.last_menu_features=dict(inventory=inventory,settings=settings,pause=pause,confirmation=confirmation,
            inventory_nav=inv_edge,inventory_return=inv_return,settings_title=settings_title,settings_return=settings_return,
            pause_left=pause_left,pause_right=pause_right,confirm_title=confirm_title,confirm_subtitle=confirm_subtitle,skipped_for_combat=False)
        return max(inventory,settings,pause,confirmation)

    def scores(self, frame):
        return self.scores_top(self.normalize_top(frame))

    def scores_top(self, top):
        health,_ = features(top)
        color=float(np.mean(cv2.dilate(health,np.ones((3,3),np.uint8))[self.masks[0]>0]>0))
        gray=cv2.cvtColor(cv2.resize(top[15:235,20:115],(48,110)),cv2.COLOR_BGR2GRAY)
        body=self.oriented_support(gray,self.body_mask,self.body_bins)
        gray_bar=cv2.cvtColor(cv2.resize(top[1:80,565:1500],(468,40)),cv2.COLOR_BGR2GRAY)
        gx=cv2.Sobel(gray_bar,cv2.CV_32F,1,0);gy=cv2.Sobel(gray_bar,cv2.CV_32F,0,1)
        edges=cv2.Canny(gray_bar,35,90)
        horizontal=np.where(np.abs(gy)>=np.abs(gx)*1.5,edges,0).astype('uint8')
        vertical=np.where(np.abs(gx)>=np.abs(gy)*1.5,edges,0).astype('uint8')
        hs=self.distance_support(horizontal,self.bar_h)
        vs=self.distance_support(vertical,self.bar_v)
        frames=min(hs,vs)  # both directions, not just one weapon/road edge
        labels=self.distance_support(cv2.Canny(cv2.cvtColor(cv2.resize(top[8:32,575:1195],(310,12)),cv2.COLOR_BGR2GRAY),35,90),self.labels)
        total=.20*body+.45*frames+.30*labels+.05*color
        self.last_features=dict(body_shape=body,bar_frames=frames,label_shape=labels,color=color,combat_score=total)
        return body,.58*frames+.42*labels

    @staticmethod
    def distance_support(edges,reference):
        if not np.any(edges):return 0.
        distance=cv2.distanceTransform(255-edges,cv2.DIST_L2,3)
        values=distance[reference>0]
        return float(np.mean(np.maximum(0,1-values/1.8))) if len(values) else 0.

    def oriented_support(self,gray,mask,bins):
        edges=cv2.Canny(gray,25,70)
        gx=cv2.Sobel(gray,cv2.CV_32F,1,0);gy=cv2.Sobel(gray,cv2.CV_32F,0,1)
        angle=(np.arctan2(gy,gx)+np.pi)%np.pi
        current=np.floor(angle/(np.pi/4)+.5).astype('uint8')%4
        total=0.;count=0
        for direction in range(4):
            reference=np.where((mask>0)&(bins==direction),255,0).astype('uint8')
            n=np.count_nonzero(reference)
            if n:
                selected=np.where(current==direction,edges,0).astype('uint8')
                total+=n*self.distance_support(selected,reference);count+=n
        return float(total/max(count,1))

def features(top):
    # Color-independent of red/yellow/green health state; localized to silhouette.
    hsv = cv2.cvtColor(top[15:235, 20:115], cv2.COLOR_BGR2HSV)
    colored = ((hsv[:,:,1] > 105) & (hsv[:,:,2] > 38)).astype('uint8')*255
    # White shortcut labels, deliberately excluding item pictures and names.
    hsv = cv2.cvtColor(top[8:32, 575:1195], cv2.COLOR_BGR2HSV)
    white = ((hsv[:,:,1] < 85) & (hsv[:,:,2] > 170)).astype('uint8')*255
    return colored, white

class ExtractionGuard:
    """Recognize a specific observed extraction sequence, not a safe zone."""
    def __init__(self):self.reset()

    def reset(self,reason='reset'):
        self.zero_start=None;self.zero_last=None;self.zero_luma=0.
        self.fade_start=None;self.previous_luma=None;self.falling=0
        self.last_confirmation=None
        self.reason=reason
        self.zero_confirmed=False

    def trace(self,now):
        return dict(phase='fade' if self.fade_start is not None else 'zero_tracking' if self.zero_last is not None else 'idle',
            reason=self.reason,zero_seen_seconds=None if self.zero_start is None else now-self.zero_start,
            fade_seconds=None if self.fade_start is None else now-self.fade_start,baseline_luma=self.zero_luma,falling_samples=self.falling,
            zero_confirmed=self.zero_confirmed)

    def update(self,now,combat_score,menu_present,cue):
        if not cue:
            self.reset('no_cue');return False
        if menu_present:
            self.reset('menu_visible');return False
        luma=cue['scene_luma']
        if cue['banner']:
            self.fade_start=None;self.falling=0
            state=cue.get('timer_state','zero' if cue['zero_score']>=.93 else 'nonzero')
            if state=='zero' and cue['zero_score']<.93:state='unknown'
            if state=='zero' and cue['zero_score']>=.93:
                if self.zero_start is None:self.zero_start=now
                self.zero_last=now;self.zero_luma=luma
                if now-self.zero_start>=.15:self.zero_confirmed=True
                self.reason='zero_tracking'
            elif state=='unknown' and self.zero_confirmed and now-self.zero_last<=1.0:
                self.reason='zero_confirmed_digits_faded'
            else:
                self.zero_start=None;self.zero_last=None;self.zero_confirmed=False;self.reason='timer_unknown' if state=='unknown' else 'timer_nonzero'
            return False
        if self.fade_start is None:
            reason=None
            if self.zero_last is None:reason='zero_not_observed'
            elif now-self.zero_last>1.0:reason='zero_evidence_expired'
            elif not self.zero_confirmed:reason='zero_seen_too_briefly'
            elif combat_score<.48:reason='hud_not_confident_at_banner_end'
            if reason:
                self.reset(reason);return False
            self.fade_start=now;self.previous_luma=luma
        if now-self.fade_start>3.0 or combat_score<=.22:
            self.reset('fade_timeout' if now-self.fade_start>3.0 else 'hud_lost_before_fade_confirmation');return False
        if luma<=self.previous_luma+.02*max(self.zero_luma,1):self.falling+=1
        else:self.falling=0
        self.previous_luma=luma
        self.reason='waiting_fade'
        # Confirm while the combat HUD still exists. Sudden HUD loss before
        # fade confirmation remains a death candidate handled by Gate.
        if now-self.fade_start>=.20 and self.falling>=3 and combat_score>=.48 and luma<=self.zero_luma*.65:
            record=dict(fade_seconds=now-self.fade_start,baseline_luma=self.zero_luma,current_luma=luma,combat_score=combat_score)
            self.reset('confirmed');self.last_confirmation=record;return True
        return False


class Gate:
    def __init__(self, confirm_frames=1, delay_ms=150):
        self.active = False
        self.missing = 0
        self.confirm_frames = confirm_frames
        self.delay_ms=delay_ms
        self.missing_since=None
        self.last_interface='combat'
        self.last_observation=None
        self.max_observation_gap=.2

    def arm(self, scores, menu_present=False, combat_score=None):
        self.active = menu_present or (combat_score>=.48 if combat_score is not None else (scores[0] >= .60 and scores[1] >= .70))
        self.missing = 0
        self.missing_since=None
        self.last_interface='menu' if menu_present else 'combat'
        self.last_observation=None
        return self.active

    def cancel(self):
        self.active = False
        self.missing = 0
        self.missing_since=None
        self.last_observation=None

    def update(self, scores, menu_present=False, now=None, combat_score=None):
        if not self.active:
            return False
        now=time.perf_counter() if now is None else now
        if combat_score is not None:
            if self.last_observation is not None and now-self.last_observation>self.max_observation_gap:
                self.missing_since=None;self.missing=0
            self.last_observation=now
            # The delay must contain continuously confirmed absence, not just
            # lack of a high-confidence recovery. Uncertainty resets the clock.
            if menu_present or combat_score>.22:
                self.missing=0;self.missing_since=None
                return False
            if combat_score<=.22:
                if self.missing_since is None:self.missing_since=now
                if (now-self.missing_since)*1000>=self.delay_ms:
                    self.cancel();return True
            return False
        if menu_present:self.last_interface='menu'
        elif scores[1]>=.70:self.last_interface='combat'
        # After a recognized menu, colored scenery is not proof that the small
        # health silhouette returned. Require the combat shortcut bar instead.
        absent=scores[1]<.30 and (scores[0]<.30 or self.last_interface=='menu') and not menu_present
        # Once absence is established, an ambiguous background match does not
        # reset it. A recognized menu or a confident combat bar resets it.
        if self.missing_since is not None and not menu_present and scores[1]<.70:
            absent=True
        self.missing=self.missing+1 if absent else 0
        if not absent:self.missing_since=None
        elif self.missing_since is None:self.missing_since=now
        if self.missing >= self.confirm_frames and (now-self.missing_since)*1000>=self.delay_ms:
            self.cancel()  # consume trigger before any external action
            return True
        return False
