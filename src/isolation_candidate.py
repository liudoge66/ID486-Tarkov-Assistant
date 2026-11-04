"""Early network-only evidence. Never authorizes process exit.

Inputs must have completed the SAME full UI/title/transition checks used by
the death owner. Elapsed capture gaps are not absence evidence.
"""
import math
import numpy as np


def reviewed_candidate(candidate, now, sequence, epoch, *, valid, features,
                       menu_score, context, cue, action, photometry, menu_intent=False,world=None):
    """Shared live/replay guard, after the decision owner's complete UI review."""
    from detector import start_hud_evidence
    # Explicit isolation contexts distinguish positive transition evidence from
    # a fresh countdown observation. Older callers keep their conservative policy.
    explicit='transition_protected' in context
    hard_guard=context.get('transition_protected',context.get('protected',True))
    protected=bool(hard_guard or menu_intent or action in ('extraction','transfer_wait','uncertain'))
    absent=not start_hud_evidence(features) and menu_score<.55
    scene_guard=bool(context.get('scene_observation_protected',hard_guard) or menu_intent or action in ('transfer_wait','uncertain'))
    episode=candidate.diagnostic_episode(now,sequence,epoch,valid,absent,scene_guard or action=='extraction')
    motion=candidate.observe_world(world,epoch,valid and not scene_guard and menu_score<.55,
                                   start_hud_evidence(features),now=now)
    if explicit:
        countdown_context=bool(context.get('countdown_observed') or cue.get('banner') or cue.get('full_observation_requested'))
        # A clock never authorizes a cutoff. During a countdown, require
        # positive scene-change evidence as well as the existing reviewed fade.
        weak_guard=bool(context.get('matched_fade_guard')) and not motion['changed']
        protected=protected or weak_guard or (countdown_context and not motion['changed'])
    else:
        countdown_context=bool(cue.get('banner') or cue.get('full_observation_requested'))
        protected=protected or now<=context.get('protected_until',-100.) or countdown_context
    hud_collapsed=(absent and isinstance(features,dict) and float(features.get('combat_score',1.))<=.05 and
                   float(menu_score)<.40 and not countdown_context)
    result=candidate.update(now,sequence,epoch,valid=valid,
        absent=absent,protected=protected,photometry=photometry,motion=motion,hud_collapsed=hud_collapsed)
    result['diagnostic_episode']=episode
    if explicit:
        result.update(transition_basis=context.get('transition_basis','none'),
                      countdown_observed=bool(countdown_context),scene_change=bool(motion['changed']))
        if protected and valid and absent:
            result['reason']='verified_transition' if hard_guard else 'awaiting_death_scene_evidence'
    return result


class IsolationCandidate:
    def __init__(self):
        self.world_reference=None;self.world_epoch=None;self.scene_changed=False;self.world_time=None
        self.diagnostic_first=None;self.diagnostic_last=None;self.diagnostic_epoch=None
        self.diagnostic_id=None;self.diagnostic_counter=0;self.diagnostic_gaps=0
        self.reset()

    def diagnostic_episode(self,now,sequence,epoch,valid,absent,normal_transition):
        # Timing only: survives evidence-chain resets, never supplies death proof.
        if self.diagnostic_epoch!=epoch or (valid and (not absent or normal_transition)):
            self.diagnostic_first=None;self.diagnostic_last=None;self.diagnostic_id=None;self.diagnostic_gaps=0
        self.diagnostic_epoch=epoch
        if valid and absent and not normal_transition:
            if self.diagnostic_first is None:
                self.diagnostic_first=now;self.diagnostic_counter+=1
                self.diagnostic_id=f'{epoch}:{self.diagnostic_counter}:{sequence}'
            if self.diagnostic_last is not None and now-self.diagnostic_last>.45:self.diagnostic_gaps+=1
            self.diagnostic_last=now
        if self.diagnostic_first is None:return None
        return dict(id=self.diagnostic_id,first_reviewed_monotonic=self.diagnostic_first,
                    elapsed_ms=round((now-self.diagnostic_first)*1000,3),capture_gaps=self.diagnostic_gaps,
                    timing_only=True)

    def observe_world(self,world,epoch,allowed,hud,now=None):
        stale=(now is not None and self.world_time is not None and not 0<=now-self.world_time<=.75)
        if not allowed or epoch!=self.world_epoch or stale:
            self.world_reference=None;self.scene_changed=False;self.world_epoch=epoch;self.world_time=None
        if allowed:self.world_time=now
        if not allowed or world is None:
            self.world_reference=None;self.scene_changed=False;self.world_time=None
            return dict(changed=False,reason='unreviewed_world')
        current=np.asarray(world,dtype=np.float32)
        if current.ndim!=2 or current.size<100 or not np.isfinite(current).all():
            self.world_reference=None;self.scene_changed=False
            return dict(changed=False,reason='invalid_world')
        if hud or self.world_reference is None or self.world_reference.shape!=current.shape:
            self.world_reference=current.copy();self.scene_changed=False
            return dict(changed=False,reason='world_reference_saved')
        reference=self.world_reference;a=reference-reference.mean();b=current-current.mean()
        denominator=float(np.linalg.norm(a)*np.linalg.norm(b))
        correlation=float(np.sum(a*b)/denominator) if denominator>1e-5 else None
        gain=float(np.sum(reference*current)/max(1e-4,float(np.sum(reference*reference))))
        error=float(np.mean(abs(current-gain*reference))/max(4.,float(reference.mean())))
        changed=(float(reference.std())>=4 and float(current.std())>=2 and
                 correlation is not None and correlation<.50 and error>.25)
        self.scene_changed=self.scene_changed or changed
        return dict(changed=bool(self.scene_changed),correlation=correlation,error=round(error,4),gain=round(gain,4))

    def reset(self, clear_episode=True):
        if clear_episode:self.episode_first=None
        self.first=None;self.first_mean=None;self.previous=None;self.peak=None;self.frames=0
        self.epoch=None;self.active=False;self.sequence=None

    def update(self, now, sequence, epoch, *, valid, absent, protected, photometry,motion=None,hud_collapsed=False):
        if not valid or protected or not absent:
            was=self.active;self.reset(clear_episode=bool(protected or (valid and not absent)))
            return dict(action='cancel' if was else 'none',reason='protected_or_recovered')
        if sequence==self.sequence:return dict(action='none',reason='duplicate_frame')
        # Two fully reviewed observations plus a changed scene can remain useful
        # on a slow frame; their elapsed gap is NOT added to Gate's death proof.
        motion=motion or dict(changed=False)
        gap_limit=.65 if motion.get('changed') else .45
        if self.epoch!=epoch or self.previous is None or not 0<now-self.previous<=gap_limit:
            was=self.active;self.reset(clear_episode=False);self.epoch=epoch
            # A broken observation chain invalidates a temporary candidate.
            cancelled=was
        else:cancelled=False
        self.sequence=sequence;self.previous=now
        if self.episode_first is None:self.episode_first=now
        try:
            mean=float(photometry['mean']);tiles=[float(x) for x in photometry['tiles']]
            if not math.isfinite(mean) or len(tiles)!=6 or not all(math.isfinite(x) for x in tiles):raise ValueError()
        except (KeyError,TypeError,ValueError):
            was=self.active;self.reset(clear_episode=False)
            return dict(action='cancel' if was or cancelled else 'none',reason='missing_photometry')
        if self.first is None:self.first=now;self.first_mean=mean
        self.frames+=1
        if self.peak is None or mean>self.peak['mean']:
            self.peak=dict(mean=mean,tiles=tiles)
        falling=sum(p>=4 and c<=p*.70 and p-c>=3 for p,c in zip(self.peak['tiles'],tiles))
        # Near-black proof still requires two independently reviewed frames.
        dark=mean<=6 and max(tiles)<=10
        trend=mean<=80 and self.peak['mean']>=12 and mean<=self.peak['mean']*.65 and falling>=4
        collapse=bool(hud_collapsed and motion.get('changed') and motion.get('error',0.)>=.45 and
                      self.frames>=3 and self.first_mean is not None and abs(mean-self.first_mean)>=1.)
        ready=self.frames>=2 and now-self.first>=.06 and (dark or trend or collapse)
        if ready and not self.active:
            self.active=True
            return dict(action='isolate',reason='reviewed_black' if dark else 'reviewed_falling_world' if trend else 'reviewed_hud_collapse_motion',
                        first_monotonic=self.first,candidate_monotonic=now,
                        observed_ms=round((now-self.first)*1000,3),frames=self.frames,mean=mean,falling_tiles=falling,
                        motion=dict(motion),capture_gap_limit_ms=gap_limit*1000)
        if cancelled:return dict(action='cancel',reason='observation_chain_changed')
        return dict(action='none',reason='already_candidate' if self.active else 'waiting_fresh_review')
