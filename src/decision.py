"""Shared visual decision policy for live capture and decoded-video replay.

No capture, input, process, or UI APIs. Only fresh, foreground observations
may call step(); pause() invalidates continuity without inventing a frame.
"""
from detector import ExtractionGuard, start_hud_evidence
from transfer import TransferFlow, resume_hud
from ui_fade import UniformFadeWatch
from interaction_ui import SteamOverlayState,confirmed_game_ui
from probe_context import finite


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

    def settlement_fast_eligible(self,now,features,menu_score,cue,hint):
        return settlement_context_eligible(now,features,menu_score,cue,hint,self.settlement_context())

    def settlement_context(self):
        protected_until=self.log_protection_until
        for stamp in (self.extraction.count_last,self.extraction.zero_last,self.menu_timer_seen):
            if stamp is not None:protected_until=max(protected_until,stamp+3.)
        return dict(active=self.gate.active,observed_at=self.last_frame,
                    last_combat=self.log_last_combat,last_menu=self.log_last_menu,
                    protected=bool(self.settings_active or self.transfer.active or self.fade_pending),
                    protected_until=protected_until)

    def isolation_context(self):
        context=self.settlement_context();now=self.last_frame
        badge=self.transfer.last_badge
        countdown=bool(now is not None and (now<=self.log_protection_until or
            (badge is not None and 0<=now-badge<=3.)))
        weak_fade=any(w.reference is not None and w.falls>=1 and w.last_gain<.98 and
            w.last_drop is not None and now is not None and 0<=now-w.last_drop<=.4
            for w in (self.hud_fade,self.esc_fade))
        strong=bool(self.settings_active or self.transfer.protected or self.fade_pending)
        context.update(transition_protected=strong,
            scene_observation_protected=bool(self.settings_active or self.transfer.protected),
            matched_fade_guard=bool(countdown and weak_fade),countdown_observed=countdown,
            transition_basis=('verified_transfer' if self.transfer.protected else
                'extraction_candidate' if self.fade_pending else 'settings' if self.settings_active else
                'weak_structural_fade' if countdown and weak_fade else 'none'))
        return context

    def relative_context(self, now, epoch, transition=False):
        # A countdown or menu intent is context, never a death exemption.
        # It only disables this shortcut; ordinary death checks stay active.
        recent_timer=any(t is not None and 0<=now-t<=3. for t in
                         (self.extraction.count_last,self.extraction.zero_last,self.menu_timer_seen))
        allowed=(self.gate.active and not transition and not self.settings_active and
                 not self.transfer.active and self.fade_pending is None and not recent_timer and
                 now>self.log_protection_until and self.last_ui_context not in ('esc','settings','inventory') and
                 self.last_frame is not None and 0<=now-self.last_frame<=.75)
        return dict(now=now,epoch=epoch,allowed=bool(allowed))

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

    def clear_absence(self):
        self.gate.missing = 0
        self.gate.missing_since = None
        self.gate.absence_observed = 0.
        self.gate.last_observation = None

    def pause(self):
        self.log_last_combat=None;self.log_last_menu=None;self.log_frames=0
        self.clear_absence()
        self.transfer.pause()
        self.extraction.reset('observation_paused')
        self.esc_fade.reset()
        self.hud_fade.reset()
        self.settings_missing = None
        self.fade_pending = None
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

    def step(self, now, scores, features, menu_score, menu_features, top,
             transfer_cue, extraction_cue, suppress_fade=False, menu_top=None,settlement_hint=None,world=None,black_confirmed=False):
        events = self.pending_events;self.pending_events=[]
        overlay=self.overlay.update(now,menu_features.get('interaction_ui',{}).get('steam'),
                                    confirmed_game_ui(features,menu_features))
        if overlay in ('paused_new','paused','waiting'):
            self.pause()
            return dict(action='monitor',
                        menu_score=1.,overlay_paused=True,events=events,
                        transfer=dict(phase=self.transfer.phase,protected=self.transfer.protected,play_enabled=False))
        if overlay=='resumed':self.pause()
        if self.last_frame is not None and now-self.last_frame > .75:
            self.log_last_combat=None;self.log_frames=0
            # A resumed frame does not prove what happened while capture stalled.
            self.esc_fade.reset()
            self.hud_fade.reset()
            self.fade_pending = None
        self.last_frame = now
        combat = features.get('combat_score', 0.)
        visible = start_hud_evidence(features)
        for stamp in (self.extraction.count_last,self.extraction.zero_last,self.menu_timer_seen):
            if stamp is not None and 0<=now-stamp<=3.:
                self.log_protection_until=max(self.log_protection_until,stamp+3.)
        if extraction_cue.get('banner') or extraction_cue.get('title_score',0)>=.8:
            self.log_protection_until=max(self.log_protection_until,now+3.)
        if visible and menu_score<.55:self.log_last_combat=now
        if menu_score>=.55:self.log_last_menu=now
        interaction_page=(self.transfer.active and self.transfer.phase in ('confirm','transfer_menu') and
                          menu_features.get('inventory_ui',{}).get('equipment_panel',{}).get('present',False))
        previous_transfer_phase=self.transfer.phase
        if interaction_page:transfer_cue=dict(transfer_cue,menu=True)
        if transfer_cue.get('menu'):
            menu_score = max(menu_score, 1.)
        absent = not visible and combat <= .22 and menu_score < .55
        transfer = self.transfer.update(now, transfer_cue, resume_hud(features), ui_absent=absent,
                                        ui_visible=visible or menu_score>=.55)
        if transfer['event']:
            events.append(dict(event=transfer['event'], phase=transfer['phase'],
                               return_evidence=transfer.get('return_evidence')))
        if interaction_page:
            transfer['page_evidence']='fresh_equipment_layout'
            if previous_transfer_phase!='transfer_menu':events.append(dict(event='transfer_interaction_page_visible',phase=transfer['phase']))
        result = dict(action='monitor', menu_score=menu_score, transfer=transfer, events=events)
        if transfer['protected']:
            self.clear_absence()
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
        self.extraction.update(now,combat,menu_score>=.55,extraction_cue)
        near_end=self.extraction.near_end(now) or self.menu_timer_near_end(now)
        observation_suppressed=(suppress_fade and not fast_transit_watch) or ui_changed
        esc = self.esc_fade.update(now, menu_top if menu_top is not None else top, menu_context, observation_suppressed)
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
                    self.fade_pending=dict(time=now,context=ui_context,evidence=esc,near_end_observed=True,last_support=now)
                    events.append(dict(event='extraction_candidate_started',reason='near_end_menu_structural_fade'))
                # Keep watching the same menu until its UI actually disappears.
                self.clear_absence();return result
            if not suppress_fade:
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
            self.extraction.reset('transit_countdown');self.fade_pending=None
        if fade['confirmed'] and not transit and near_end and not observation_suppressed:
            if self.fade_pending is None:
                self.fade_pending=dict(time=now,context=ui_context,evidence=fade,near_end_observed=True,last_support=now)
                events.append(dict(event='extraction_candidate_started',reason='near_end_hud_structural_fade'))
        if self.fade_pending:
            pending=self.fade_pending
            current=esc if pending['context'] in ('esc','settings') else fade
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
            # A confirmed near-end structural fade can outlive the last readable
            # timer glyph by one sample. Don't discard it at the glyph's TTL
            # boundary immediately before the HUD disappears on a slow machine.
            # This is a bounded observed sequence, never a general safe zone.
            support_age=now-pending.get('last_support',pending['time'])
            # Only a fully certified black frame may bridge one slow capture
            # after an ALREADY confirmed near-end structural fade. This never
            # creates a candidate or extends its original one-second deadline.
            terminal_black=(black_confirmed and absent and not observation_suppressed and
                            pending.get('min_gain',1.)<=.65 and 0<=support_age<=.75)
            supported=(pending.get('near_end_observed',False) and now-pending['time']<=1.
                       and (support_age<=.5 or terminal_black))
            restarted=(extraction_cue.get('banner') and self.extraction.count_value is not None
                       and self.extraction.count_value>1)
            recovered=(visible and combat>=.48 and current.get('reason')=='baseline_updated')
            invalid=(ui_changed or suppress_fade or (not near_end and not supported) or now-pending['time']>1.
                     or rebound or restarted or recovered)
            if invalid:
                events.append(dict(event='extraction_candidate_cancelled',reason='transition_timer_expired_or_brightened',
                                   pending_age_ms=round((now-pending['time'])*1000,3),support_age_ms=round(support_age*1000,3),
                                   near_end=bool(near_end),ui_changed=bool(ui_changed),suppressed=bool(suppress_fade),
                                   rebound=bool(rebound),restarted=bool(restarted),recovered=bool(recovered)))
                self.fade_pending=None
            elif absent and now-pending['time']>=.06:
                evidence=dict(policy='near_end_structural_fade_then_ui_absent',
                              terminal_black_bridge=bool(terminal_black and support_age>.5),
                              countdown=self.extraction.trace(now),menu_timer=self.menu_timer_value,
                              **pending['evidence'])
                self.fade_pending=None;self.gate.cancel()
                result.update(action='extraction',evidence=evidence);return result
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
