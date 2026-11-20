"""Shared visual decision policy for live capture and decoded-video replay.

No capture, input, process, or UI APIs. Only fresh, foreground observations
may call step(); pause() invalidates continuity without inventing a frame.
"""
from detector import ExtractionGuard, start_hud_evidence
from transfer import TransferFlow, resume_hud
from ui_fade import UniformFadeWatch
from interaction_ui import SteamOverlayState,confirmed_game_ui
from probe_context import finite

# ---- Shared timing policy (seconds) -----------------------------------------
# One name per concept, so every death lane reads the same number.
FRESH_FRAME=.75          # a frame older than this proves nothing about "now"
COUNTDOWN_CONTEXT=3.     # an observed extraction/menu countdown blocks the shortcut death lanes this long
# Extraction latch: once a countdown reached its end and the screen faded with
# HUD/scene structure kept, the following blackout is extraction, not death.
# It resolves on fresh evidence, never on a wall-clock expiry that hands the
# same blackout to the ordinary death lane (the 2026-11-04 22:44 false exit).
LATCH_FRAME_BUDGET=8     # give up only after this many fresh frames ...
LATCH_MIN_SECONDS=2.     # ... AND this long (2x the old 1 s expiry), so neither a fast
                         # replay nor one slow capture can exhaust the latch
LATCH_WALL_CAP=6.        # safety cap while frames stall; ends as "uncertain", never death
# Countdown-deadline latch (build 68). The structural fade check fails when the camera turns
# or a screen it does not watch is open (2026-11-19 16:27). A countdown read on >=3
# consistent decrements gives its expected zero time. HUD/menu last seen no earlier than
# DEADLINE_BEFORE before that zero, and gone within DEADLINE_AFTER after it, opens the same
# latch. Losing the UI earlier than that is still judged as death.
DEADLINE_STEPS=3
DEADLINE_BEFORE=.3
DEADLINE_AFTER=2.5
EXTRACTION_WIDGET_PRIORITY=1.   # transit badge ignored this long after a fresh extraction-widget reading
# Optional "extraction auto-pause" (off by default). While a live extraction
# countdown or long transit countdown is on screen, NO death lane runs. The
# player accepts that dying inside the zone is not handled.
ZONE_FRESH=.75           # a countdown reading this recent means "in the zone"
ZONE_STALE=1.            # no countdown this long ...
ZONE_LEAVE_HUD=1.        # ... plus full combat HUD this long = player walked out
ZONE_RETURN_HUD=3.       # same, after focus was lost inside the zone (countdown may have been missed)
ZONE_DONE_FRAMES=2       # HUD gone (not a menu) on this many fresh frames ...
ZONE_DONE_SECONDS=.10    # ... spanning this long = extracted/transferred -> low-frequency standby
ZONE_LIMBO=10.           # stale countdown, neither clean HUD nor clean absence: give up to standby
ZONE_DONE_MARGIN=1.      # HUD gone no later than this before the countdown's zero = extraction/transit finished;
                         # earlier (or no countdown timing) = unconfirmed (death, disconnect or quit inside the zone)
# Low-frequency standby after a zone ends: sample every few seconds, resume on a full HUD seen twice.
ZONE_STANDBY_INTERVAL=5. # seconds between standby samples
ZONE_STANDBY_CONFIRM_GAP=.3   # second confirming sample follows the first this fast
ZONE_STANDBY_CONFIRM_MAX=1.   # ... and the pair must fit in this window


def settlement_context_eligible(now,features,menu_score,cue,hint,context):
    """Same conservative policy for main-thread and independent fresh probes."""
    if not isinstance(hint,dict) or not isinstance(context,dict) or not context.get('active') or not finite(now):return False
    if not isinstance(features,dict) or not isinstance(cue,dict) or not finite(menu_score):return False
    if any(not finite(features.get(k)) for k in ('combat_score','body_shape','bar_frames','label_shape')):return False
    observed=context.get('observed_at')
    if not finite(observed) or not 0<=now-observed<=.75:return False
    if not finite(hint.get('seen_monotonic')):return False
    age=now-hint['seen_monotonic']
    if not -.05<=age<=1. or hint.get('menu_intent') or context.get('menu_intent') or not hint.get('full_frame'):return False
    combat=context.get('last_combat');menu=context.get('last_menu')
    # A closing raid may publish its request before the renderer removes the
    # last HUD frame. Current HUD resets the consecutive proof; it must not
    # permanently invalidate a still-fresh request.
    if not finite(combat) or not 0<now-combat<=2.:return False
    if menu is not None and (not finite(menu) or menu>=combat):return False
    until=context.get('protected_until',-100.)
    if not finite(until) or context.get('protected') or now<=until:return False
    if hint.get('guard_epoch') is not None and hint['guard_epoch']!=context.get('guard_epoch'):return False
    if menu_score>=.55 or start_hud_evidence(features):return False
    if any(features.get(k,0)>=limit for k,limit in (('body_shape',.2),('bar_frames',.15),('label_shape',.2))):return False
    if cue.get('banner') or cue.get('title_score',0)>=.8:return False
    return True


class DecisionEngine:
    def __init__(self, gate):
        self.gate = gate
        self.overlay = SteamOverlayState()
        self.transfer = TransferFlow()
        self.extraction = ExtractionGuard()
        self.esc_fade = UniformFadeWatch()
        self.hud_fade = UniformFadeWatch()
        self.settings_active = False
        self.settings_missing = None
        self.last_frame = None
        self.fade_pending = None
        self.deadline=None        # dict(at=expected zero, source, steps) of a consistently decreasing countdown
        self.last_ui_seen=None    # last frame with combat HUD or a recognised menu
        self.extraction_widget_seen=None
        self.last_ui_context = None
        self.settings_last_visible = None
        self.display_intent_until = None
        self.display_dirty = False
        self.pending_events = []
        self.menu_timer_value = None
        self.menu_timer_seen = None
        self.menu_timer_deadline = None
        self.menu_timer_steps = 0
        self.log_last_combat=None;self.log_last_menu=None
        self.log_token=None;self.log_frames=0;self.log_frame_time=None
        self.log_protection_until=-100.
        self.position_guard=False
        self.zone_enabled=False   # user switch "监控自动暂停"
        self.zone=None            # dict while auto-paused in an extraction/transit zone

    @property
    def zone_kind(self):
        return None if self.zone is None else self.zone['kind']

    def zone_signal(self,now):
        """'extraction' / 'transfer' while a live countdown is observed, else None."""
        e=self.extraction
        if (e.count_value is not None and e.count_last is not None and 0<=now-e.count_last<=ZONE_FRESH
                and (e.count_steps>=1 or self.zone is not None)):
            return 'extraction'
        t=self.transfer
        if (t.kind=='countdown' and t.last_badge is not None and 0<=now-t.last_badge<=ZONE_FRESH
                and (t.decreases>=1 or self.zone is not None)):
            return 'transfer'
        return None

    def zone_step(self,now,result,events,combat,visible,absent,menu_score,extraction_cue,suppress_fade,features):
        """Returns a finished result while auto-paused, or None to run the normal policy."""
        self.extraction.update(now,combat,menu_score>=.55,extraction_cue)
        if not self.zone_enabled:
            self.zone=None;return None
        kind=self.zone_signal(now)
        if kind:
            if self.zone is None:
                self.zone=dict(kind=kind,since=now,seen=now,hud_since=None,absent_since=None,absent_frames=0,
                               limbo=0.,last=now,returned=False,deadline=self.zone_deadline(kind))
                self.clear_absence();self.hud_fade.reset();self.esc_fade.reset();self.fade_pending=None
                events.append(dict(event='zone_entered',zone=kind))
            else:
                self.zone['seen']=now;self.zone['kind']=kind
            deadline=self.zone_deadline(kind)
            if deadline is not None:self.zone['deadline']=deadline
        if self.zone is None:return None
        z=self.zone;stale=now-z['seen'];elapsed=max(0.,now-z['last']);z['last']=now
        self.clear_absence();self.fade_pending=None
        # Walked out: countdown gone and complete combat HUD held.
        complete=menu_score<.55 and resume_hud(features)
        if complete and stale>=ZONE_STALE:
            if z['hud_since'] is None:z['hud_since']=now
        else:z['hud_since']=None
        need=ZONE_RETURN_HUD if z['returned'] else ZONE_LEAVE_HUD
        if z['hud_since'] is not None and now-z['hud_since']>=need:
            events.append(dict(event='zone_left',zone=z['kind'],stale_ms=round(stale*1000,1)))
            self.zone=None;self.extraction.reset('zone_left');self.transfer.reset()
            self.hud_fade.reset();self.esc_fade.reset()
            result['action']='monitor';return result
        # Extracted / transferred / died inside: HUD gone and it is not a menu.
        if absent and not suppress_fade:
            if z['absent_since'] is None:z['absent_since']=now;z['absent_frames']=0
            z['absent_frames']+=1
            if z['absent_frames']>=ZONE_DONE_FRAMES and now-z['absent_since']>=ZONE_DONE_SECONDS:
                return self.zone_finish(result,events,'hud_gone',now)
        else:z['absent_since']=None;z['absent_frames']=0
        # Neither: only counts while no menu is open, so a long inventory visit never ends the pause.
        if stale>=ZONE_STALE and not complete and not absent and menu_score<.55 and not suppress_fade:
            z['limbo']+=min(elapsed,.5)
            if z['limbo']>=ZONE_LIMBO:return self.zone_finish(result,events,'unresolved',now)
        result['action']='zone_pause';result['zone']=z['kind'];return result

    def zone_deadline(self,kind):
        """Expected zero time of the countdown seen on this frame (perf_counter seconds), or None."""
        if kind=='extraction':
            e=self.extraction
            if e.count_value is not None and e.count_since is not None:return e.count_since+e.count_value
        elif kind=='transfer':
            t=self.transfer
            if t.value is not None and t.last_badge is not None:return t.last_badge+t.value
        return None

    def zone_finish(self,result,events,reason,now=None):
        kind=self.zone_kind;deadline=self.zone['deadline'] if self.zone else None
        # Display only: whether the HUD vanished at the countdown's end. Never changes what the pause does.
        remaining=None if deadline is None or now is None else round(deadline-now,3)
        confirmed=bool(reason=='hud_gone' and remaining is not None and remaining<=ZONE_DONE_MARGIN)
        events.append(dict(event='zone_done',zone=kind,reason=reason,confirmed=confirmed,remaining_s=remaining))
        self.zone=None;self.gate.cancel();self.transfer.reset();self.extraction.reset('zone_done')
        result.update(action='zone_done',zone=kind,reason=reason,confirmed=confirmed,remaining_s=remaining);return result

    def standby_resume(self,scores,features,menu_present=False):
        """A new match's full HUD was confirmed during standby: return to ordinary monitoring."""
        self.zone=None;self.fade_pending=None;self.deadline=None;self.last_ui_seen=None;self.settings_active=False;self.settings_missing=None
        self.transfer.reset();self.extraction.reset('standby_resume')
        self.esc_fade.reset();self.hud_fade.reset();self.layout_changed()
        self.overlay=SteamOverlayState()
        return self.gate.arm(scores,menu_present=menu_present,combat_score=features['combat_score'],features=features)

    def set_position_guard(self,active):
        active=bool(active)
        if active==self.position_guard:return False
        self.position_guard=active;self.clear_absence()
        self.log_last_combat=None;self.log_frames=0;self.log_frame_time=None
        self.esc_fade.reset();self.hud_fade.reset();self.fade_pending=None;self.deadline=None;self.last_frame=None
        return True

    def settlement_fast_eligible(self,now,features,menu_score,cue,hint):
        return settlement_context_eligible(now,features,menu_score,cue,hint,self.settlement_context())

    def guards(self):
        """Single source of truth for 'why a death lane must not fire right now'.

        Each consumer below used to re-list these members in its own words.
        """
        countdowns=[t for t in (self.extraction.count_last,self.extraction.zero_last,self.menu_timer_seen) if t is not None]
        return dict(position=self.position_guard,settings=self.settings_active,
                    transfer_active=self.transfer.active,transfer_protected=self.transfer.protected,
                    extraction_latch=self.fade_pending is not None,zone=self.zone is not None,
                    countdown_until=max([self.log_protection_until]+[t+COUNTDOWN_CONTEXT for t in countdowns]))

    def settlement_context(self):
        g=self.guards()
        return dict(active=self.gate.active,observed_at=self.last_frame,
                    last_combat=self.log_last_combat,last_menu=self.log_last_menu,
                    protected=bool(g['position'] or g['zone'] or g['settings'] or g['transfer_active'] or g['extraction_latch']),
                    protected_until=g['countdown_until'])

    def isolation_context(self):
        context=self.settlement_context();now=self.last_frame;g=self.guards()
        badge=self.transfer.last_badge
        countdown=bool(now is not None and (now<=self.log_protection_until or
            (badge is not None and 0<=now-badge<=COUNTDOWN_CONTEXT)))
        weak_fade=any(w.reference is not None and w.falls>=1 and w.last_gain<.98 and
            w.last_drop is not None and now is not None and 0<=now-w.last_drop<=.4
            for w in (self.hud_fade,self.esc_fade))
        scene=bool(g['position'] or g['zone'] or g['settings'] or g['transfer_protected'])
        context.update(transition_protected=scene or g['extraction_latch'],scene_observation_protected=scene,
            matched_fade_guard=bool(countdown and weak_fade),countdown_observed=countdown,
            transition_basis=('lighthouse_position' if g['position'] else 'zone_'+self.zone_kind if g['zone'] else 'verified_transfer' if g['transfer_protected'] else
                'extraction_candidate' if g['extraction_latch'] else 'settings' if g['settings'] else
                'weak_structural_fade' if countdown and weak_fade else 'none'))
        return context

    def relative_context(self, now, epoch, transition=False):
        # A countdown or menu intent is context, never a death exemption.
        # It only disables this shortcut; ordinary death checks stay active.
        g=self.guards()
        allowed=(self.gate.active and not transition and not (g['position'] or g['zone'] or g['settings'] or g['transfer_active'] or g['extraction_latch'])
                 and now>g['countdown_until'] and self.last_ui_context not in ('esc','settings','inventory') and
                 self.last_frame is not None and 0<=now-self.last_frame<=FRESH_FRAME)
        return dict(now=now,epoch=epoch,allowed=bool(allowed))

    @staticmethod
    def new_latch(now,context,evidence):
        return dict(time=now,context=context,evidence=evidence,last_support=now,last_seen=now,frames=0)

    def begin_display_switch(self, now, source):
        self.settings_active = True
        self.settings_missing = None
        self.display_intent_until = now+2.
        self.layout_changed()
        self.pending_events.append(dict(event='display_switch_guard_started',source=source))

    def menu_timer_near_end(self, now):
        return (self.menu_timer_deadline is not None and self.menu_timer_steps>=1 and
                self.menu_timer_seen is not None and now-self.menu_timer_seen<=1.0 and self.menu_timer_value<=1.0 and
                -1.<=self.menu_timer_deadline-now<=1.0)

    def observe_menu_timer(self, now, value):
        if value is None:
            if self.menu_timer_seen is not None and now-self.menu_timer_seen>1.:
                self.menu_timer_value=None;self.menu_timer_seen=None;self.menu_timer_deadline=None;self.menu_timer_steps=0
            return
        if self.menu_timer_value is None or value>self.menu_timer_value+.3:
            self.menu_timer_steps=0;self.menu_timer_deadline=now+value
        elif value<self.menu_timer_value-.04:
            self.menu_timer_steps+=1;self.menu_timer_deadline=now+value
        self.menu_timer_value=value;self.menu_timer_seen=now

    def track_deadline(self,now):
        """Remember the expected zero of a countdown seen decreasing consistently; forget it when contradicted or long past."""
        e=self.extraction;reading=None
        if e.count_value is not None and e.count_since is not None and e.count_last is not None and now-e.count_last<=.05:
            reading=('extraction',e.count_since+e.count_value,e.count_steps)
        elif (self.menu_timer_deadline is not None and self.menu_timer_seen is not None and now-self.menu_timer_seen<=.05):
            reading=('menu',self.menu_timer_deadline,self.menu_timer_steps)
        if reading is not None:
            source,at,steps=reading
            if steps>=DEADLINE_STEPS:self.deadline=dict(at=at,source=source,steps=steps)
            elif self.deadline is not None and abs(at-self.deadline['at'])>1.:self.deadline=None  # restarted / different countdown
        if self.deadline is not None and now-self.deadline['at']>DEADLINE_AFTER:self.deadline=None

    def deadline_window(self,now):
        """Evidence dict when this UI loss belongs to the countdown's end, else None."""
        d=self.deadline
        if d is None or self.last_ui_seen is None:return None
        seen=self.last_ui_seen-d['at'];gone=now-d['at']
        if not (-DEADLINE_BEFORE<=seen<=DEADLINE_AFTER and -DEADLINE_BEFORE<=gone<=DEADLINE_AFTER):return None
        return dict(policy='countdown_deadline_then_ui_absent',source=d['source'],steps=d['steps'],
                    ui_last_seen_vs_zero_ms=round(seen*1000,1),absent_vs_zero_ms=round(gone*1000,1))

    def clear_absence(self):
        self.gate.missing = 0
        self.gate.missing_since = None
        self.gate.absence_observed = 0.
        self.gate.last_observation = None

    def pause(self):
        if self.zone is not None:self.zone.update(returned=True,hud_since=None,absent_since=None,absent_frames=0,limbo=0.)
        self.log_last_combat=None;self.log_last_menu=None;self.log_frames=0
        self.clear_absence()
        self.transfer.pause()
        self.extraction.reset('observation_paused')
        self.esc_fade.reset()
        self.hud_fade.reset()
        self.settings_missing = None
        self.fade_pending = None
        self.deadline=None;self.last_ui_seen=None;self.extraction_widget_seen=None
        self.last_frame = None
        self.last_ui_context=None
        self.menu_timer_value=None;self.menu_timer_seen=None;self.menu_timer_deadline=None;self.menu_timer_steps=0

    def layout_changed(self):
        self.log_last_combat=None;self.log_frames=0
        self.clear_absence()
        self.menu_timer_value=None;self.menu_timer_seen=None;self.menu_timer_deadline=None;self.menu_timer_steps=0
        self.last_ui_context=None
        self.extraction.reset('layout_changed')
        self.esc_fade.reset()
        self.hud_fade.reset()
        self.fade_pending = None
        self.deadline=None;self.last_ui_seen=None

    def step(self, now, scores, features, menu_score, menu_features, top,
             transfer_cue, extraction_cue, suppress_fade=False, menu_top=None,settlement_hint=None,world=None,black_confirmed=False):
        events = self.pending_events;self.pending_events=[]
        if self.position_guard:
            self.clear_absence();self.last_frame=now
            return dict(action='position_wait',menu_score=1.,events=events,
                transfer=dict(phase=self.transfer.phase,protected=True,event=None,play_enabled=False))
        overlay=self.overlay.update(now,menu_features.get('interaction_ui',{}).get('steam'),
                                    confirmed_game_ui(features,menu_features))
        if overlay in ('paused_new','paused','waiting'):
            self.pause()
            return dict(action='monitor',
                        menu_score=1.,overlay_paused=True,events=events,
                        transfer=dict(phase=self.transfer.phase,protected=self.transfer.protected,play_enabled=False))
        if overlay=='resumed':self.pause()
        if self.last_frame is not None and now-self.last_frame > FRESH_FRAME:
            self.log_last_combat=None;self.log_frames=0
            # A resumed frame does not prove what happened while capture stalled.
            # An already established extraction latch survives: it is resolved
            # by the fresh frames that follow, within its own budgets.
            self.esc_fade.reset()
            self.hud_fade.reset()
        self.last_frame = now
        combat = features.get('combat_score', 0.)
        visible = start_hud_evidence(features)
        for stamp in (self.extraction.count_last,self.extraction.zero_last,self.menu_timer_seen):
            if stamp is not None and 0<=now-stamp<=COUNTDOWN_CONTEXT:
                self.log_protection_until=max(self.log_protection_until,stamp+COUNTDOWN_CONTEXT)
        if extraction_cue.get('banner') or extraction_cue.get('title_score',0)>=.8:
            self.log_protection_until=max(self.log_protection_until,now+COUNTDOWN_CONTEXT)
        if visible and menu_score<.55:self.log_last_combat=now
        if menu_score>=.55:self.log_last_menu=now
        interaction_page=(self.transfer.active and self.transfer.phase in ('confirm','transfer_menu') and
                          menu_features.get('inventory_ui',{}).get('equipment_panel',{}).get('present',False))
        previous_transfer_phase=self.transfer.phase
        if interaction_page:transfer_cue=dict(transfer_cue,menu=True)
        if transfer_cue.get('menu'):
            menu_score = max(menu_score, 1.)
        absent = not visible and combat <= .22 and menu_score < .55
        if extraction_cue.get('banner') and extraction_cue.get('center_value') is not None:self.extraction_widget_seen=now
        if ((transfer_cue.get('countdown') or transfer_cue.get('confirm')) and self.extraction_widget_seen is not None
                and 0<=now-self.extraction_widget_seen<=EXTRACTION_WIDGET_PRIORITY):
            transfer_cue=dict(transfer_cue,countdown=False,confirm=False,suppressed='extraction_widget')
        if visible or menu_score>=.55:self.last_ui_seen=now
        transfer = self.transfer.update(now, transfer_cue, resume_hud(features), ui_absent=absent,
                                        ui_visible=visible or menu_score>=.55)
        if transfer['event']:
            events.append(dict(event=transfer['event'], phase=transfer['phase'],
                               return_evidence=transfer.get('return_evidence')))
        if interaction_page:
            transfer['page_evidence']='fresh_equipment_layout'
            if previous_transfer_phase!='transfer_menu':events.append(dict(event='transfer_interaction_page_visible',phase=transfer['phase']))
        result = dict(action='monitor', menu_score=menu_score, transfer=transfer, events=events)
        if self.zone_enabled or self.zone is not None:
            zoned=self.zone_step(now,result,events,combat,visible,absent,menu_score,extraction_cue,suppress_fade,features)
            if zoned is not None:return zoned
        if transfer['protected']:
            self.clear_absence();self.deadline=None
            self.extraction.reset('transfer_protected')
            self.esc_fade.reset()
            self.hud_fade.reset()
            self.fade_pending = None
            result['action'] = 'transfer_wait'
            return result
        if transfer['play_enabled']:
            self.gate.arm(scores, features=features)
            self.layout_changed()
            self.settings_active = False

        settings_data=menu_features.get('settings_ui',{})
        settings_visible = menu_features.get('settings', 0.) >= .55 or settings_data.get('graphics',False)
        if settings_visible:self.settings_last_visible=now
        self.observe_menu_timer(now,settings_data.get('countdown'))
        if visible and menu_score<.55 and not extraction_cue.get('banner',False):
            self.menu_timer_value=None;self.menu_timer_seen=None;self.menu_timer_deadline=None;self.menu_timer_steps=0
        # Native graphics changes are positive evidence; merely visiting a menu is not.
        if settings_data.get('display_changed'):
            self.display_dirty=True;self.begin_display_switch(now,'display_values_changed')
        if settings_data.get('apply_clicked') and settings_data.get('graphics') and self.display_dirty:
            self.begin_display_switch(now,'changed_display_values_applied')
        if self.pending_events:events.extend(self.pending_events);self.pending_events=[]
        if self.settings_active:
            if visible and menu_score<.55:
                self.settings_active=False;self.settings_missing=None;self.display_dirty=False
                self.layout_changed();events.append(dict(event='settings_transition_resumed'))
            elif menu_score>=.55:
                self.settings_missing=None
                if self.display_intent_until is not None and now>self.display_intent_until:
                    self.settings_active=False;events.append(dict(event='display_switch_menu_stable'))
            else:
                if self.settings_missing is None:self.settings_missing=now
                if now-self.settings_missing>=5.:
                    result.update(action='uncertain',reason='显示切换后持续无法确认画面，请回到战斗HUD后重新开启')
                    events.append(dict(event='display_switch_unresolved'));self.gate.cancel();return result
            if self.settings_active:
                self.clear_absence();self.extraction.reset('verified_display_switch')
                self.esc_fade.reset();self.hud_fade.reset();self.fade_pending=None
                result['action']='settings_wait';return result

        settings_recent=(self.settings_last_visible is not None and now-self.settings_last_visible<=.6)
        esc_present = (menu_features.get('pause', 0.) >= .55 or
                       menu_features.get('esc_title', {}).get('present', False))
        menu_context='settings' if settings_visible or settings_recent else 'esc' if esc_present else None
        paired = features.get('bar_frames', 0.) >= .22 and features.get('label_shape', 0.) >= .35
        transfer_remaining=(self.transfer.value-(now-self.transfer.last_badge)
            if self.transfer.value is not None and self.transfer.last_badge is not None else None)
        # A positively observed, near-end transit must be watched immediately on
        # focus return. This only enables observation; it grants no death immunity.
        fast_transit_watch=(self.transfer.kind=='countdown' and self.transfer.last_badge is not None and
                            now-self.transfer.last_badge<=.4 and transfer_remaining is not None and
                            -.25<=transfer_remaining<=2.)
        ui_context=menu_context or ('inventory' if menu_score>=.55 else 'combat' if visible else self.last_ui_context or 'combat')
        ui_changed=(visible or menu_score>=.55) and self.last_ui_context is not None and ui_context!=self.last_ui_context
        self.last_ui_context=ui_context
        if ui_changed or (suppress_fade and not fast_transit_watch):
            if self.fade_pending:events.append(dict(event='extraction_candidate_cancelled',reason='interface_changed'))
            self.fade_pending=None;self.hud_fade.reset();self.esc_fade.reset()
        if not self.zone_enabled:self.extraction.update(now,combat,menu_score>=.55,extraction_cue)
        near_end=self.extraction.near_end(now) or self.menu_timer_near_end(now)
        self.track_deadline(now)
        observation_suppressed=(suppress_fade and not fast_transit_watch) or ui_changed
        # Inventory open when an extraction ends: the whole screen fades uniformly (2026-11-18 15:10).
        # Watched only near a countdown's end (or while its latch is held); it can open the extraction
        # latch but never confirms a death by itself.
        pending_inventory=self.fade_pending is not None and self.fade_pending['context']=='inventory'
        inventory_watch=(menu_context is None and menu_score>=.55 and menu_features.get('inventory',0.)>=.55
                         and (near_end or fast_transit_watch or pending_inventory))
        fade_context=menu_context or ('inventory' if inventory_watch or pending_inventory else None)
        esc = self.esc_fade.update(now, menu_top if menu_top is not None else top, fade_context, observation_suppressed)
        pending_combat=self.fade_pending is not None and self.fade_pending['context']=='combat'
        watching_combat=(self.hud_fade.context=='combat' and self.hud_fade.reference is not None and
                         (self.hud_fade.falls>=1 or (near_end and features.get('label_shape',0.)>=.30)))
        fade = self.hud_fade.update(now, top, 'combat' if (paired or pending_combat or watching_combat) and menu_score < .55 else None, observation_suppressed,world=world)
        result.update(early_esc_fade=esc, hud_preserving_fade=fade,fast_transit_watch=fast_transit_watch)
        # A menu can fade on death too. An observed countdown far from its end
        # does not exempt death. Near-end classification uses progression, not OCR zero.
        if esc['confirmed']:
            if transfer_remaining is not None and transfer_remaining<=1.25 and self.transfer.confirm_fade(now,esc):
                self.clear_absence();events.append(dict(event='transfer_loading',phase=self.transfer.phase,evidence=esc))
                result.update(action='transfer_wait',transfer=dict(phase=self.transfer.phase,protected=True,event='transfer_loading',play_enabled=False));return result
            if not observation_suppressed and near_end and not self.transfer.recent_countdown(now):
                if self.fade_pending is None:
                    self.fade_pending=self.new_latch(now,ui_context,esc)
                    events.append(dict(event='extraction_candidate_started',reason='near_end_menu_structural_fade'))
                # Keep watching the same menu until its UI actually disappears.
                self.clear_absence();return result
            if not suppress_fade and self.fade_pending is None and esc.get('context')!='inventory':
                self.gate.cancel();events.append(dict(event='settings_death_fade_confirmed' if menu_context=='settings' else 'esc_death_fade_confirmed',evidence=esc,timer_value=self.menu_timer_value))
                result.update(action='death',evidence=esc);return result
        if self.transfer.confirm_fade(now, fade):
            self.clear_absence()
            self.extraction.reset('transfer_loading')
            self.fade_pending = None
            events.append(dict(event='transfer_loading', phase=self.transfer.phase, evidence=fade,
                               countdown_decreases=self.transfer.decreases, last_value=self.transfer.value))
            result.update(action='transfer_wait', transfer=dict(phase=self.transfer.phase, protected=True,
                          event='transfer_loading', play_enabled=False))
            return result

        transit = self.transfer.recent_countdown(now) or fast_transit_watch
        if transit:
            self.extraction.reset('transit_countdown');self.fade_pending=None;self.deadline=None
        if fade['confirmed'] and not transit and near_end and not observation_suppressed:
            if self.fade_pending is None:
                self.fade_pending=self.new_latch(now,ui_context,fade)
                events.append(dict(event='extraction_candidate_started',reason='near_end_hud_structural_fade'))
        if (self.fade_pending is None and absent and not transit and not suppress_fade and not ui_changed):
            window=self.deadline_window(now)
            if window is not None:
                self.fade_pending=self.new_latch(now,'deadline',window)
                events.append(dict(event='extraction_candidate_started',reason='countdown_deadline_ui_absent',**window))
        if self.fade_pending:
            pending=self.fade_pending
            current=esc if pending['context'] in ('esc','settings','inventory') else fade
            # Once the near-end fade was confirmed, a still-matched darker
            # image also supports continuity below the initial 22% gain floor.
            # It cannot create an extraction candidate by itself.
            if current.get('confirmed') or (current.get('matched') and .03<current.get('gain',0)<pending['evidence'].get('gain',1.)+.08):
                pending['last_support']=now
            gain=current.get('gain')
            if gain is None and current.get('gains'):gain=sum(current['gains'])/len(current['gains'])
            minimum=pending.get('min_gain',pending['evidence'].get('gain',1.))
            rebound=gain is not None and gain>minimum+.08
            if gain is not None and (current.get('confirmed') or current.get('matched')):
                pending['min_gain']=min(minimum,gain)
            support_age=now-pending.get('last_support',pending['time'])
            age=now-pending['time']
            if now>pending['last_seen']:pending['frames']+=1;pending['last_seen']=now
            # Counter-evidence: the player is still in the raid.
            restarted=(extraction_cue.get('banner') and self.extraction.count_value is not None
                       and self.extraction.count_value>1)
            # A fading HUD keeps single channels above start thresholds for
            # several frames, so "HUD visible" alone is NOT recovery.
            recovered=(visible and combat>=.48 and current.get('reason')=='baseline_updated')
            if pending['context']=='deadline' and (visible or menu_score>=.55):recovered=True  # UI came back: still in the raid
            cancel=ui_changed or suppress_fade or rebound or restarted or recovered
            if cancel:
                events.append(dict(event='extraction_candidate_cancelled',reason='extraction_counter_evidence',
                                   pending_age_ms=round(age*1000,3),support_age_ms=round(support_age*1000,3),frames=pending['frames'],
                                   near_end=bool(near_end),ui_changed=bool(ui_changed),suppressed=bool(suppress_fade),
                                   rebound=bool(rebound),restarted=bool(restarted),recovered=bool(recovered)))
                if pending['context']=='deadline':self.deadline=None
                self.fade_pending=None
            elif absent and age>=.06:
                evidence=dict(policy='near_end_structural_fade_then_ui_absent',latch_frames=pending['frames'],
                              latch_age_ms=round(age*1000,3),countdown=self.extraction.trace(now),menu_timer=self.menu_timer_value,
                              latch_context=pending['context'],
                              **{('latch_policy' if k=='policy' else k):v for k,v in pending['evidence'].items()})
                self.fade_pending=None;self.gate.cancel()
                result.update(action='extraction',evidence=evidence);return result
            elif (pending['frames']>=LATCH_FRAME_BUDGET and age>=LATCH_MIN_SECONDS) or age>=LATCH_WALL_CAP:
                # Neither extraction nor a return to the raid was observed. Stop
                # without exiting: guessing "death" here is what caused false exits.
                events.append(dict(event='extraction_latch_unresolved',frames=pending['frames'],latch_age_ms=round(age*1000,3)))
                self.fade_pending=None;self.gate.cancel()
                result.update(action='uncertain',reason='撤离过程未能确认，已停止监控（未执行退出），请确认后重新开启')
                return result
            else:
                # Latch held: this frame is part of the extraction blackout and
                # feeds no death lane (ordinary absence, relative fade or log fast path).
                self.clear_absence();return result
        relative=features.get('relative_death',{})
        if relative.get('certified') and absent and all(features.get(k,0)<v for k,v in (('body_shape',.2),('bar_frames',.15),('label_shape',.2))) and not transit and not self.transfer.active and not suppress_fade and not near_end and self.fade_pending is None:
            self.gate.cancel();events.append(dict(event='relative_death_confirmed',evidence=relative))
            result.update(action='death',evidence=relative);return result
        ordinary_death=self.gate.update(scores,menu_present=menu_score>=.55,now=now,
                                         combat_score=combat,features=features)
        if ordinary_death:
            self.log_frames=0;self.log_frame_time=None
            result['action']='death';return result
        if self.settlement_fast_eligible(now,features,menu_score,extraction_cue,settlement_hint):
            token=(settlement_hint['session'],settlement_hint['request_id'])
            if token!=self.log_token:
                self.log_token=token;self.log_frames=0;self.log_frame_time=None
            if self.log_frame_time is None or now-self.log_frame_time>=.015:
                self.log_frames+=1;self.log_frame_time=now
            if self.log_frames>=2 and now>=settlement_hint['seen_monotonic']:
                self.gate.cancel();result['action']='death'
                events.append(dict(event='settlement_visual_confirmed',request_id=settlement_hint['request_id'],
                                   log_lag_ms=settlement_hint.get('log_lag_ms'),
                                   capture_after_read_ms=round((now-settlement_hint['seen_monotonic'])*1000,3),
                                   fresh_absent_frames=self.log_frames))
            return result
        self.log_frames=0;self.log_frame_time=None
        return result
