from relative_fade import RelativeFadeWatch
from hsv_fade import HsvFadeWatch
from dark_trend import DarkTrendWatch
"""Visual-only HUD detector. No keyboard or process APIs."""
from pathlib import Path
import time
import cv2
import numpy as np
from timer_digits import TimerDigits
from center_extraction import CenterExtractionTimer
from candidate_world import preview as candidate_world_preview
from inventory_ui import InventoryUI
from settings_ui import SettingsUI
from interaction_ui import InteractionUI
from visual_absence import black_scene
from parallel_review import review_ui
from hud_layout import candidates as layout_candidates,project as project_layout
cv2.setNumThreads(1)

def combat_hud_evidence(features):
    """First arming needs independent battle-HUD evidence, never menus alone."""
    return (features.get('combat_score',0)>=.48 and features.get('body_shape',0)>=.45
            and features.get('bar_frames',0)>=.22 and features.get('label_shape',0)>=.35)

def start_hud_evidence(features):
    """Arming accepts any one HUD channel; death thresholds are independent."""
    return any(features.get(key,0)>=threshold for key,threshold in
               [('combat_score',.48),('body_shape',.45),('bar_frames',.22),('label_shape',.35)])

class ArmConfirmation:
    def __init__(self,hz=30):self.first=None;self.last=None;self.frames=0
    def update(self,features,now):
        if not start_hud_evidence(features):return False
        self.first=self.last=now;self.frames=1;return True
    def summary(self):
        return dict(frames=self.frames,stable_ms=0,required_ms=0,min_frames=1,
                    requires='any: combat >= .48 OR body >= .45 OR bar frames >= .22 OR label edges >= .35')

class Detector:
    def __init__(self, asset_dir=None, *, parallel_ui=True):
        self.parallel_ui=parallel_ui;self.last_parallel=dict(used=False)
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
        self.inventory_ui=InventoryUI(root);self.last_inventory={};self.settings_ui=SettingsUI(root);self.observe_settings=False
        self.interaction_ui=InteractionUI(root);self.last_interaction={}
        self.center_timer=CenterExtractionTimer(root,self.settings_ui.numbers)
        self.reset_layout()

    def reset_layout(self):
        self.extraction_full_until=0.;self.center_preferred_until=0.;self.center_scan_time=None;self.center_frame_key=None;self.center_frame_result=None
        self.center_timer.reset()
        self.relative_watch=RelativeFadeWatch();self.hsv_watch=HsvFadeWatch();self.dark_watch=DarkTrendWatch();self.relative_context=None
        self.layout=None;self.layout_info={};self.menu_top=None;self.extraction_top=None
        self._esc_track=None;self.last_esc_fast=dict(used=False,reason='reset')
        self._combat_layout=None;self.last_death_fast=dict(used=False,reason='reset')

    def prepare_full_review(self):
        # Keep only the expiring live references. Prior negative UI review and
        # temporal proof cannot authorize anything in the expanded scope.
        self.relative_watch.reset_proof('capture_scope_expanded')
        self.hsv_watch.clear_proof('capture_scope_expanded')
        self.hsv_watch.started=None;self.hsv_watch.baseline=None
        self.dark_watch.restart_observation('capture_scope_expanded')

    def known_combat_black_frame(self,frame,full_size):
        """Only accelerate an observation; the shared engine still decides."""
        self.last_death_fast=dict(used=False,reason='no_combat_layout');self._same_frame_menu=None;self._same_frame_proj=None
        layout=self._combat_layout
        if not layout or (layout['width'],layout['height'])!=tuple(full_size):return None
        if frame.shape[0]<full_size[1] or frame.shape[1]<full_size[0]:
            self.last_death_fast['reason']='incomplete_frame';return None
        top,menu,extract,info=project_layout(frame,layout)
        self._same_frame_proj=(id(frame),dict(layout),top,menu,extract,dict(info))
        scores=self.scores_top(top)
        if start_hud_evidence(self.last_features):
            self.last_death_fast['reason']='hud_visible';return None
        menu_score=self.menu_score_top(menu)
        self._same_frame_menu=(id(frame),menu.copy(),menu_score,dict(self.last_menu_features))
        if menu_score>=.55:
            self.last_death_fast['reason']='menu_visible';return None
        scene=black_scene(frame,full_size,layout)
        if not scene['certified']:
            self.last_death_fast=dict(used=False,reason=scene['reason'],scene=scene);return None
        self.layout_recovery=None;self.layout_info=info;self.menu_layout_info=info
        self.last_top=top;self.menu_top=menu;self.extraction_top=extract
        self.last_features['layout']=dict(info);self.last_extraction=self.extraction_features(extract)
        self.last_inventory=dict(observed=False,complete=False,present=False,popup_count=0,certified_black=True)
        self.last_interaction=dict(observed=True,steam=False,btr=False,certified_black=True)
        self.last_menu_features.update(inventory_ui=self.last_inventory,
            settings_ui=dict(observed=False,graphics=False,display_changed=False,apply_box=None,countdown=None))
        self._esc_track=None;self.last_esc_fast=dict(used=False,reason='black_scene')
        self.last_death_fast=dict(used=True,reason='certified_black_scene',scene=scene)
        return scores,menu_score

    @staticmethod
    def esc_preview(frame):
        # Fixed-size scene evidence, independent of display brightness. This is
        # only a shortcut eligibility check, never menu or death evidence.
        h,w=frame.shape[:2]
        ys=np.linspace(0,h-1,90,dtype=np.int32);xs=np.linspace(0,w-1,160,dtype=np.int32)
        return cv2.cvtColor(frame[ys[:,None],xs[None,:],:3],cv2.COLOR_BGR2GRAY).astype('float32')

    def confirmed_esc_frame(self,frame,full_size):
        track=self._esc_track
        self.last_esc_fast=dict(used=False,reason='no_confirmed_esc')
        if not track:return None
        if tuple(full_size)!=track['size'] or frame.shape[0]<full_size[1]-2:
            self.last_esc_fast['reason']='capture_changed';return None
        current=self.esc_preview(frame);base=track['preview'];mask=base>24
        if np.count_nonzero(mask)<300:
            self.last_esc_fast['reason']='insufficient_scene';return None
        gain=float(np.median(current[mask]/(base[mask]+.001)))
        error=np.abs(current-gain*base)
        # A new settings page or item window must take the full path. Uniform
        # fading is allowed here, but fresh logo evidence is still required below.
        if not .15<=gain<=1.8 or float(np.percentile(error,95))>10 or float(np.mean(error>18))>.015:
            self.last_esc_fast['reason']='scene_changed';return None
        top,_,extract,info=project_layout(frame,track['combat_layout'])
        _,menu,_,menu_info=project_layout(frame,track['menu_layout'])
        scores=self.scores_top(top)
        if start_hud_evidence(self.last_features):
            self.last_esc_fast['reason']='hud_returned';return None
        score=self.menu_score_top(menu);features=self.last_menu_features
        if features.get('pause',0)<.55 or max(features.get(k,0) for k in ('inventory','settings','confirmation','console'))>=.55:
            self.last_esc_fast['reason']='anchors_changed';return None
        # Keep the original full Steam/BTR recognizer on EVERY fast frame.
        # A dimmed game logo can remain visible beneath the Steam overlay.
        interaction=self.interaction_ui.detect(frame,full_size,info)
        self.layout_recovery=None;self.layout_info=info;self.menu_layout_info=menu_info
        self.last_top=top;self.menu_top=menu;self.extraction_top=extract
        self.last_features['layout']=dict(info);self.last_extraction=self.extraction_features(extract)
        self.last_inventory=dict(observed=False,complete=False,present=False,popup_count=0,skipped_for_esc=True)
        self.last_interaction=interaction
        if interaction.get('steam') or interaction.get('btr'):
            self._esc_track=None
            self.last_menu_features=dict(interaction_ui=interaction,inventory=0.,settings=0.,pause=0.,confirmation=0.)
            self.last_esc_fast=dict(used=True,reason='interaction_override');return scores,1.
        features['inventory_ui']=self.last_inventory
        features['settings_ui']=dict(observed=False,graphics=False,display_changed=False,apply_box=None,countdown=None,skipped_for_esc=True)
        track['preview']=current
        self.last_esc_fast=dict(used=True,reason='fresh_esc_anchors',gain=round(gain,4))
        return scores,score

    def remember_esc(self,frame,full_size):
        f=self.last_menu_features;settings=f.get('settings_ui',{})
        if (frame.shape[0]>=full_size[1]-2 and f.get('pause',0)>=.55 and
            max(f.get(k,0) for k in ('inventory','settings','confirmation','console'))<.55 and
            not self.last_inventory.get('present') and not settings.get('graphics') and
            settings.get('countdown') is None and not settings.get('display_changed') and
            not self.last_interaction.get('steam') and not self.last_interaction.get('btr')):
            self._esc_track=dict(size=tuple(full_size),preview=self.esc_preview(frame),
                combat_layout={k:self.layout_info[k] for k in ('left','right','top','width','height')},
                menu_layout={k:self.menu_layout_info[k] for k in ('left','right','top','width','height')})

    def normalize_top(self,frame,full_size=None):
        h,w=frame.shape[:2];full_size=full_size or (w,h)
        self.layout_recovery=None
        if self.layout is not None and (self.layout['width'],self.layout['height'])!=tuple(full_size):self.reset_layout()
        current=None
        if self.layout is not None:
            proj=getattr(self,'_same_frame_proj',None)
            if proj is not None and proj[0]==id(frame) and proj[1]==self.layout:
                top,menu,extract,info=proj[2],proj[3],proj[4],dict(proj[5])
            else:
                top,menu,extract,info=project_layout(frame,self.layout)
            self.scores_top(top)
            current=(self.last_features['combat_score'],top,menu,extract,info,self.layout,dict(self.last_features))
            # A good lock remains fixed. Weak locks can be corrected only by a
            # substantially better, independently matched battle HUD in this frame.
            if self.layout_hud_evidence(self.last_features):
                self.menu_top=menu;self.extraction_top=extract;self.layout_info=info
                return top
        best=current
        for layout in layout_candidates(frame,full_size):
            if current is not None and layout==self.layout:continue
            top,menu,extract,info=project_layout(frame,layout)
            self.scores_top(top)
            score=self.last_features['combat_score'];feature=dict(self.last_features)
            if current is not None:
                if not self.layout_hud_evidence(feature) or score<current[0]+.10:continue
            if best is None or score>best[0]:best=(score,top,menu,extract,info,layout,feature)
        _,top,self.menu_top,self.extraction_top,self.layout_info,layout,feature=best
        if current is not None and layout!=self.layout:
            self.layout_recovery=dict(previous=dict(self.layout),corrected=dict(layout),
                                      previous_score=round(current[0],4),corrected_score=round(best[0],4))
            self.layout=layout
        elif self.layout_hud_evidence(feature):self.layout=layout
        return top

    @staticmethod
    def layout_hud_evidence(features):
        return (features.get('combat_score',0)>=.48 and features.get('bar_frames',0)>=.22
                and features.get('label_shape',0)>=.35)

    def lock_current_layout(self):
        if self.layout is None and self.layout_info:
            self.layout={key:self.layout_info[key] for key in ('left','right','top','width','height')}

    def analyze(self, frame, full_size=None):
        context=self.relative_context;hsv_stats=None;dark_stats=None;pending_dark=None;pending_dark_key=None
        self.last_relative_fade=dict(certified=False,reason='not_enabled')
        size=full_size or (frame.shape[1],frame.shape[0])
        full=frame.shape[:2]==(size[1],size[0])
        self.last_world_gray=None
        self.last_candidate_world_gray=candidate_world_preview(frame,self._combat_layout or self.layout or self.layout_info) if full else None
        if full:
            h,w=frame.shape[:2]
            self.last_world_gray=cv2.cvtColor(cv2.resize(frame[round(h*.30):round(h*.86),round(w*.08):round(w*.92),:3],(192,96),interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY)
        layout=self._combat_layout
        if context and full and layout and (layout['width'],layout['height'])==tuple(size):
            now=context['now'];key=(context['epoch'],tuple(layout[k] for k in ('left','right','top','width','height')))
            top,menu,extract,info=project_layout(frame,layout)
            scores=self.scores_top(top);menu_score=self.menu_score_top(menu)
            absent=(not start_hud_evidence(self.last_features) and self.last_features.get('combat_score',0)<=.22 and menu_score<.55 and
                    all(self.last_features.get(k,0)<v for k,v in (('body_shape',.2),('bar_frames',.15),('label_shape',.2))))
            cue=self.merge_center_timer(frame,size,self.extraction_features(extract))
            allowed=context['allowed'] and not cue.get('banner') and cue.get('title_score',0)<.8
            result=self.relative_watch.candidate(now,frame,allowed=allowed,absent=absent,key=key)
            hsv_stats=self.hsv_watch.measure(frame)
            hsv=self.hsv_watch.candidate_stats(now,hsv_stats,allowed=allowed,absent=absent,key=key)
            if hsv['certified']:
                result=dict(hsv,legacy=dict(result))
            else:
                result['hsv']=hsv
            dark_stats=self.dark_watch.measure(frame)
            dark=self.dark_watch.candidate_stats(now,dark_stats,allowed=allowed,absent=absent,key=key)
            if dark['certified']:result=dict(dark,legacy=dict(result))
            else:result['dark_trend']=dark
            self.last_relative_fade=result
            if result['certified']:
                # Cheap text/layout veto remains on the current frame. A new
                # panel never inherits an older frame's negative review.
                from settlement_fast import FastProbe
                possible_title=FastProbe.possible_title(frame,layout)
                if result.get('path')=='dark_trend' and possible_title:
                    pending_dark=dict(result,review='full_detector_then_worker_title');pending_dark_key=key
                    result.update(certified=False,reason='dark_trend_requires_full_review')
                else:
                    interaction,inventory,_,review_ms=review_ui(
                        lambda:self.interaction_ui.detect(frame,size,dict(layout)),
                        lambda:self.inventory_ui.detect(frame,size,dict(layout),return_score=self.last_menu_features.get('inventory_return',0.)))
                    result['ui_review_ms']=review_ms['wall_ms']
                    if interaction.get('steam') or interaction.get('btr') or inventory.get('present'):
                        result.update(certified=False,reason='current_frame_ui_visible')
                        self.hsv_watch.reset('current_frame_ui_visible')
                        self.dark_watch.reset('current_frame_ui_visible')
                    elif possible_title:
                        result.update(certified=False,reason='new_text_or_panel_ambiguity')
                        self.hsv_watch.reset('new_text_or_panel_ambiguity')
                        self.dark_watch.reset('new_text_or_panel_ambiguity')
                    else:
                        self.last_parallel=dict(used=False);self.layout_recovery=None
                        self.layout_info=info;self.menu_layout_info=info
                        self.last_top=top;self.menu_top=menu;self.extraction_top=extract
                        self.last_features['layout']=dict(info);self.last_features['relative_death']=dict(result)
                        self.last_extraction=cue
                        self.last_inventory=dict(observed=False,complete=False,present=False,popup_count=0)
                        self.last_interaction=dict(observed=True,steam=False,btr=False)
                        self.last_menu_features.update(inventory_ui=self.last_inventory,
                            settings_ui=dict(observed=False,graphics=False,display_changed=False,apply_box=None,countdown=None))
                        self.last_death_fast=dict(used=result.get('path')!='dark_trend',reason=result['reason'],scene=dict(result))
                        self.last_esc_fast=dict(used=False,reason='relative_fade_confirmed');self._esc_track=None
                        return scores,menu_score
        result=self._analyze_original(frame,full_size)
        if full and self.last_candidate_world_gray is None:
            self.last_candidate_world_gray=candidate_world_preview(frame,self._combat_layout or self.layout or self.layout_info)
        self.last_extraction=self.merge_center_timer(frame,size,self.last_extraction)
        if context and full and self._combat_layout:
            layout=self._combat_layout
            key=(context['epoch'],tuple(layout[k] for k in ('left','right','top','width','height')))
            features=self.last_features
            visible=start_hud_evidence(features) and result[1]<.55
            absent=not start_hud_evidence(features) and features.get('combat_score',0)<=.22 and result[1]<.55
            cue=self.last_extraction
            self.relative_watch.reviewed_frame(context['now'],frame,visible=visible,absent=absent,key=key,
                allowed=context['allowed'] and not cue.get('banner') and cue.get('title_score',0)<.8)
            # HSV cannot borrow a negative review from inventory/Steam/BTR or
            # settings pixels, even when legacy top-menu scores are low.
            interaction=self.last_interaction;inventory=self.last_inventory
            settings=self.last_menu_features.get('settings_ui',{})
            hsv_allowed=(context['allowed'] and not cue.get('banner') and cue.get('title_score',0)<.8
                and not interaction.get('steam') and not interaction.get('btr') and not inventory.get('present')
                and not settings.get('graphics') and not settings.get('display_changed'))
            self.hsv_watch.reviewed_stats(context['now'],hsv_stats if hsv_stats is not None else self.hsv_watch.measure(frame),
                visible=visible,absent=absent,key=key,allowed=hsv_allowed)
            self.dark_watch.reviewed_stats(context['now'],dark_stats if dark_stats is not None else self.dark_watch.measure(frame),
                visible=visible,absent=absent,key=key,allowed=hsv_allowed)
            if (pending_dark and hsv_allowed and result[1]<.55 and
                key==pending_dark_key and key==self.dark_watch.key and not visible and features.get('combat_score',0)<=.22 and
                all(features.get(k,0)<v for k,v in (('body_shape',.2),('bar_frames',.15),('label_shape',.2)))):
                self.last_features['relative_death']=dict(pending_dark)
                self.last_relative_fade=dict(pending_dark)
                # last_death_fast stays unchanged: this path must still pass
                # the worker's current-frame title/transition/ownership checks.
                return result
        self.last_features.pop('relative_death',None)
        return result

    def _analyze_original(self, frame, full_size=None):
        self.last_parallel=dict(used=False);self.last_same_frame_menu_reused=False
        full_size=full_size or (frame.shape[1],frame.shape[0])
        dark=self.known_combat_black_frame(frame,full_size)
        if dark is not None:return dark
        quick=self.confirmed_esc_frame(frame,full_size)
        if quick is not None:return quick
        fallback=dict(self.last_esc_fast);self._esc_track=None
        top=self.normalize_top(frame,full_size)
        self.last_esc_fast=fallback
        self.last_top=top  # owned resize output; safe for a bounded diagnostic ring
        self.last_extraction=self.extraction_features(self.extraction_top)
        scores=self.scores_top(top)
        self.last_features['layout']=dict(self.layout_info)
        self.last_inventory=dict(observed=False,complete=False,present=False,popup_count=0)
        self.last_interaction=dict(observed=False,steam=False,btr=False)
        # A confident combat HUD already prevents absence. Avoid expensive logo
        # searches in this common case; inspect menus only when HUD is unclear.
        if self.last_features['combat_score']>=.48:
            if frame.shape[0]>=full_size[1]-2:
                self.last_interaction=self.interaction_ui.detect(frame,full_size,self.layout_info,steam_only=True)
                if self.last_interaction.get('steam') or self.last_interaction.get('btr'):
                    self.last_menu_features=dict(interaction_ui=self.last_interaction,inventory=0.,settings=0.,pause=0.,confirmation=0.)
                    return scores,1.
            self._combat_layout={k:self.layout_info[k] for k in ('left','right','top','width','height')}
            self.observe_settings=False;self.settings_ui.masks=None;self.settings_ui.pending_masks=None
            self.last_menu_features=dict(inventory=0.,settings=0.,pause=0.,confirmation=0.,skipped_for_combat=True)
            return scores,0.
        cached=getattr(self,'_same_frame_menu',None)
        self.last_same_frame_menu_reused=bool(cached is not None and cached[0]==id(frame) and
            cached[1].shape==self.menu_top.shape and np.array_equal(cached[1],self.menu_top))
        if self.last_same_frame_menu_reused:
            menu=cached[2];self.last_menu_features=dict(cached[3])
        else:menu=self.menu_score_top(self.menu_top)
        self._same_frame_menu=None
        self.menu_layout_info=dict(self.layout_info)
        # Some menu pages span the full window even when combat is pillarboxed.
        # Compare menu anchors independently; never rewrite the combat viewport
        # using menu text as HUD evidence.
        fw,fh=full_size or (frame.shape[1],frame.shape[0])
        if self.layout_info.get('left',0)!=0 or self.layout_info.get('right',fw)!=fw or self.layout_info.get('top',0)!=0:
            _,full_menu,_,full_info=project_layout(frame,dict(left=0,right=fw,top=0,width=fw,height=fh))
            previous=dict(self.last_menu_features)
            full_score=self.menu_score_top(full_menu)
            if full_score>=.55 and full_score>menu+.05:
                menu=full_score;self.menu_top=full_menu;self.menu_layout_info=full_info
            else:self.last_menu_features=previous
        # A centered 16:9 menu canvas may sit over a full-width ultrawide
        # world. Background outside it need not be black. This is menu-only.
        if fw/fh>1.85 and menu<.80:
            margin=max(0,round((fw-fh*16/9)/2))
            _,center_menu,_,center_info=project_layout(frame,dict(left=margin,right=fw-margin,top=0,width=fw,height=fh))
            previous=dict(self.last_menu_features)
            center_score=self.menu_score_top(center_menu)
            if center_score>=.55 and center_score>menu+.05:
                menu=center_score;self.menu_top=center_menu;self.menu_layout_info=center_info
            else:self.last_menu_features=previous
        needs_settings=(self.observe_settings or self.last_menu_features.get('settings',0)>=.55 or self.last_menu_features.get('pause',0)>=.55)
        saved_settings=(self.settings_ui.masks,self.settings_ui.pending_masks)
        return_score=self.last_menu_features.get('inventory_return',0.)
        if self.parallel_ui and frame.shape[0]>=full_size[1]-2:
            interaction,inventory,settings,self.last_parallel=review_ui(
                lambda:self.interaction_ui.detect(frame,full_size,dict(self.layout_info)),
                lambda:self.inventory_ui.detect(frame,full_size,dict(self.layout_info),return_score=return_score),
                (lambda:self.settings_ui.detect(frame,full_size)) if needs_settings else None)
            if interaction.get('steam') or interaction.get('btr') or inventory.get('present'):
                self.settings_ui.masks,self.settings_ui.pending_masks=saved_settings
        else:
            interaction=self.interaction_ui.detect(frame,full_size,self.layout_info)
            inventory=None;settings=None
            if not (interaction.get('steam') or interaction.get('btr')):
                inventory=self.inventory_ui.detect(frame,full_size,self.layout_info,return_score=return_score)
                if needs_settings and not inventory['present']:settings=self.settings_ui.detect(frame,full_size)
        self.last_interaction=interaction
        if interaction.get('steam') or interaction.get('btr'):
            self.last_menu_features=dict(interaction_ui=interaction,inventory=0.,settings=0.,pause=0.,confirmation=0.)
            return scores,1.
        self.last_inventory=inventory
        self.last_menu_features['inventory_ui']=self.last_inventory
        if self.last_inventory['present']:menu=max(menu,.95)
        if self.last_menu_features.get('settings',0)>=.55 or self.last_menu_features.get('pause',0)>=.55:self.observe_settings=True
        if needs_settings and not inventory['present']:
            self.last_menu_features['settings_ui']=settings
            if settings.get('graphics'):menu=max(menu,.95)
        self.remember_esc(frame,full_size)
        if menu<.55 and start_hud_evidence(self.last_features):
            self._combat_layout={k:self.layout_info[k] for k in ('left','right','top','width','height')}
        return scores,menu

    def merge_center_timer(self,frame,full_size,cue):
        cue=dict(cue);now=self.relative_context['now'] if self.relative_context else time.perf_counter()
        if cue.get('banner') or cue.get('title_score',0)>=.8:self.extraction_full_until=max(self.extraction_full_until,now+4.)
        layout=self.layout or self._combat_layout or self.layout_info
        key=(now,frame.ctypes.data,frame.shape,tuple(layout.get(k) for k in ('left','right','top','width','height')) if layout else None)
        full=frame.shape[:2]==(full_size[1],full_size[0])
        if key==self.center_frame_key:
            center=dict(self.center_frame_result)
        elif full and (now<self.extraction_full_until or self.center_scan_time is None or now-self.center_scan_time>=1. or (self.center_frame_key is not None and key[-1]!=self.center_frame_key[-1])):
            center=self.center_timer.detect(frame,full_size,layout,observed_at=now);self.center_scan_time=now
            self.center_frame_key=key;self.center_frame_result=dict(center)
        else:center=dict(observed=False,present=False,reason='needs_full_frame' if not full else 'scan_not_due')
        cue['top_banner']=cue.get('banner',False);cue['top_timer_state']=cue.get('timer_state');cue['top_timer_digits']=list(cue.get('timer_digits',[]));cue['center_timer']=center
        if center.get('present'):
            self.extraction_full_until=max(self.extraction_full_until,now+4.)
            self.center_preferred_until=max(self.center_preferred_until,now+4.)
            cue['banner']=True;cue['timer_source']='center'
            value=center.get('value');cue['center_value']=value
            cue['timer_state']='unknown' if value is None else 'zero' if value==0 else 'nonzero'
            cue['timer_digits']=[];cue['timer_text']=None if value is None else f'{value:.1f}'
            cue['zero_score']=center.get('confidence',0.) if value==0 else 0.
        elif now<self.extraction_full_until:
            # Never overwrite a fresh decimal center reading with the top
            # counter's stale integer value while the widgets disappear.
            cue.update(timer_source='center_unreadable',center_value=None,timer_state='unknown',timer_digits=[],zero_score=0.)
        else:cue['timer_source']='top'
        cue['full_observation_requested']=now<self.extraction_full_until
        return cue

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
        self.normalize_top(frame)
        return self.menu_score_top(self.menu_top)

    def menu_score_top(self, top):
        # All anchors stay inside the existing top strip. Ignore selected-tab
        # grayscale polarity by matching inventory text edges instead.
        gray=cv2.cvtColor(top,cv2.COLOR_BGR2GRAY)
        console=self.console_header(gray)
        if console['present']:
            self.last_menu_features=dict(console=1.,console_features=console,inventory=0.,settings=0.,pause=0.,confirmation=0.,skipped_for_combat=False)
            return 1.
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
            pause_left=pause_left,pause_right=pause_right,confirm_title=confirm_title,confirm_subtitle=confirm_subtitle,skipped_for_combat=False,console_features=console)
        return max(inventory,settings,pause,confirmation)

    @staticmethod
    def console_header(gray):
        # The empty command strip remains present without typed text or hints.
        # Ignore the FPS overlay on the left. Require a broad flat dark strip
        # ending in a strong, aligned horizontal discontinuity, not darkness alone.
        band=gray[2:28,700:1780].astype('float32')
        median=float(np.median(band));flat=float(np.mean(np.abs(band-median)<=2))
        bottom_flat=float(np.mean(np.abs(gray[26:29,700:1780].astype('float32')-median)<=2))
        below=gray[30:34,700:1780].astype('float32').mean(axis=0)
        boundary=float(np.mean(np.abs(below-median)>=7))
        present=18<=median<=115 and flat>=.93 and bottom_flat>=.78 and boundary>=.75
        return dict(present=bool(present),level=round(median,2),flat_fraction=round(flat,4),bottom_flat=round(bottom_flat,4),boundary_fraction=round(boundary,4))

    def scores(self, frame):
        return self.scores_top(self.normalize_top(frame))

    def scores_top(self, top):
        cached=getattr(self,'_last_scores_top',None)
        if cached is not None and cached[0].shape==top.shape and np.array_equal(cached[0],top):
            self.last_features=dict(cached[1])
            return cached[2]
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
        out=(body,.58*frames+.42*labels)
        self._last_scores_top=(top.copy(),dict(self.last_features),out)
        return out

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
        self.count_value=None;self.count_since=None;self.count_steps=0;self.count_start=None;self.count_luma=0.;self.count_last=None

    def trace(self,now):
        return dict(phase='fade' if self.fade_start is not None else 'zero_tracking' if self.zero_last is not None else 'idle',
            reason=self.reason,zero_seen_seconds=None if self.zero_start is None else now-self.zero_start,
            fade_seconds=None if self.fade_start is None else now-self.fade_start,baseline_luma=self.zero_luma,falling_samples=self.falling,
            zero_confirmed=self.zero_confirmed,countdown_value=self.count_value,countdown_steps=self.count_steps,
            expected_zero=None if self.count_since is None else self.count_since+self.count_value)

    def track_countdown(self,now,cue,luma):
        # A sequence of confident decrements can establish timing when the final
        # glyph fades before 00. Unknown glyphs never become zero evidence.
        digits=cue.get('timer_digits',[])
        if cue.get('timer_source')=='center':
            value=cue.get('center_value')
            if value is None or cue.get('timer_state') not in ('nonzero','zero'):return False
            value=float(value)
        else:
            if cue.get('timer_state') not in ('nonzero','zero') or len(digits)!=2:return False
            value=10*digits[0]+digits[1]
        maximum=CenterExtractionTimer.MAX_SECONDS if cue.get('timer_source')=='center' else 30
        if not 0<=value<=maximum:return False
        if self.count_last is not None and now-self.count_last>.75:
            self.reset('countdown_observation_gap')
        decreased=self.count_value is not None and value<self.count_value
        if decreased and abs((self.count_value-value)-(now-self.count_since))>1.5:
            self.reset('countdown_inconsistent_jump');decreased=False
        if self.count_value is None:
            self.count_value=value;self.count_since=now;self.count_start=now;self.count_steps=0
        elif value!=self.count_value:
            elapsed=now-self.count_since
            if decreased:self.count_steps+=1
            else:self.count_steps=0;self.count_start=now
            self.count_value=value;self.count_since=now
        self.count_luma=luma;self.count_last=now
        return decreased

    def near_end(self, now):
        # Never extrapolate an old mid-countdown reading all the way to zero.
        return (self.count_value is not None and self.count_last is not None and
                0 <= now-self.count_last <= 1.0 and self.count_value <= 1 and
                (self.count_steps >= 1 or self.zero_confirmed))

    def update(self,now,combat_score,menu_present,cue):
        """Observe only. Timing/luma alone never authorize extraction."""
        cue=cue or {}
        if cue.get('banner'):
            self.track_countdown(now,cue,cue.get('scene_luma',0.))
            if cue.get('timer_state')=='zero' and cue.get('zero_score',0)>=.8:
                if self.zero_start is None:self.zero_start=now
                self.zero_last=now
                if now-self.zero_start>=.15:self.zero_confirmed=True
            else:
                self.zero_start=None;self.zero_last=None;self.zero_confirmed=False
            self.reason='near_end' if self.near_end(now) else 'countdown_tracking'
        elif self.count_last is not None:
            # A visible battle frame without the banner proves departure, except
            # the short final fade whose last reliable reading was already <=1.
            if (not menu_present and combat_score>.22 and not self.near_end(now)) or now-self.count_last>1.:
                self.reset('countdown_left_or_expired')
        return False



class Gate:
    def __init__(self, confirm_frames=1, delay_ms=150):
        self.active = False
        self.missing = 0
        self.confirm_frames = confirm_frames
        self.delay_ms=delay_ms
        self.missing_since=None
        self.last_interface='combat'
        self.last_observation=None;self.absence_observed=0.
        self.max_observation_gap=.2

    def arm(self, scores, menu_present=False, combat_score=None,features=None):
        evidence=start_hud_evidence(features) if features is not None else (combat_score>=.48 and scores[0]>=.60 and scores[1]>=.40 if combat_score is not None else scores[0]>=.60 and scores[1]>=.70)
        self.active = bool(evidence and not menu_present)
        self.missing = 0
        self.missing_since=None
        self.last_interface='menu' if menu_present else 'combat'
        self.last_observation=None;self.absence_observed=0.
        return self.active

    def cancel(self):
        self.active = False
        self.missing = 0
        self.missing_since=None
        self.last_observation=None;self.absence_observed=0.

    def update(self, scores, menu_present=False, now=None, combat_score=None,features=None):
        if not self.active:
            return False
        now=time.perf_counter() if now is None else now
        if combat_score is not None:
            delta=0. if self.last_observation is None else max(0.,now-self.last_observation)
            self.last_observation=now
            # The delay must contain continuously confirmed absence, not just
            # lack of a high-confidence recovery. Uncertainty resets the clock.
            self.last_visibility={key:features.get(key,0) for key in ('combat_score','body_shape','bar_frames','label_shape')} if features is not None else None
            hud_visible=start_hud_evidence(features) if features is not None else False
            if menu_present or hud_visible or combat_score>.22:
                self.missing=0;self.missing_since=None;self.absence_observed=0.
                return False
            if combat_score<=.22:
                if self.missing_since is None:self.missing_since=now;self.absence_observed=0.
                if self.missing>0:
                    # Keep prior confirmed evidence on a slow machine, but do not
                    # turn a long observation gap into confirmed absence time.
                    self.absence_observed+=min(delta,max(.12,self.max_observation_gap))
                self.missing+=1
                if self.missing>=2 and self.absence_observed*1000>=self.delay_ms:
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
