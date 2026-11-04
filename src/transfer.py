"""Visible transfer-page/countdown cues and a bounded-evidence transit state machine."""
from pathlib import Path
import cv2
import numpy as np

class TransferDetector:
    def __init__(self,assets=None):
        root=Path(assets or Path(__file__).parent/'assets')/'transfer'
        self.templates={n:cv2.imdecode(np.frombuffer((root/(n+'.png')).read_bytes(),dtype=np.uint8),0)
                        for n in ('equipment_cn','transfer_cn','return_cn','countdown_suffix_cn','confirm_cn','running_icon')}
        bank=np.load(root/'digits.npz')
        self.digits=list(zip(bank['labels'],bank['images'],bank['ratios']))
        self.badges=[(kind,scale,cv2.resize(self.templates[name],None,fx=scale,fy=scale)) for kind,name in (('countdown','countdown_suffix_cn'),('confirm','confirm_cn')) for scale in (.8,1.,1.15,1.3)]
        self.badge_edges=[cv2.Canny(template,20,60) for _,_,template in self.badges]
    @staticmethod
    def match(region,template):
        if region is None or region.shape[0]<template.shape[0] or region.shape[1]<template.shape[1]:return 0.
        return max(0.,float(cv2.minMaxLoc(cv2.matchTemplate(region,template,cv2.TM_CCOEFF_NORMED))[1]))
    def detect(self,top,frame,full_size,layout=None):
        out=dict(badge_observed=False,menu=False,countdown=False,equipment_score=0.,title_score=0.,return_score=0.,countdown_score=0.)
        if top is not None:
            gray=cv2.cvtColor(top[:,:,:3],cv2.COLOR_BGR2GRAY)
            out['equipment_score']=self.match(gray[:65,:230],self.templates['equipment_cn'])
            out['title_score']=self.match(gray[20:105,900:1700],self.templates['transfer_cn'])
            out['return_score']=max(self.match(gray[:70,1690:1920],self.templates['return_cn']),self.match(255-gray[:70,1690:1920],self.templates['return_cn']))
            out['title_edge_score']=self.edge(gray[20:105,900:1700],self.templates['transfer_cn'])[0]
            out['menu']=out['equipment_score']>=.50 and out['return_score']>=.48 and (out['title_score']>=.72 or out['title_edge_score']>=.64)
        if out['menu']:return out
        fw,fh=full_size
        if frame is None or frame.shape[0]<round(fh*.94):return out
        out['badge_observed']=True
        left,right,y=0,fw,0
        if layout:left,right,y=layout['left'],layout['right'],layout['top']
        aw=right-left;ah=fh-2*y
        roi=frame[y+round(ah*.77):y+round(ah*.94),left+round(aw*.22):left+round(aw*.78),:3]
        if not roi.size:return out
        # Search glyph structure directly; hue is neither a prerequisite nor a verdict.
        roi=cv2.resize(roi,None,fx=1920/aw,fy=1920/aw)
        gray=cv2.cvtColor(roi,cv2.COLOR_BGR2GRAY)
        edges=cv2.Canny(gray,20,60)
        for (kind,scale,template),template_edge in zip(self.badges,self.badge_edges):
                _,score,_,(x,yy)=cv2.minMaxLoc(cv2.matchTemplate(edges,template_edge,cv2.TM_CCORR_NORMED))
                h,w=template.shape
                if not .35*gray.shape[1]<x<.70*gray.shape[1] or score<.40:continue
                # Running-man icon must occupy the same text row to its left.
                il=max(0,x-round(150*scale));ir=x if kind=='confirm' else max(il,x-round(25*scale))
                iy=max(0,yy-round(8*scale));iy2=min(gray.shape[0],yy+h+round(8*scale))
                icon=cv2.resize(self.templates['running_icon'],None,fx=scale,fy=scale)
                icon_score,(ix,iy0)=self.edge(gray[iy:iy2,il:ir],icon)
                if icon_score<.43:continue
                nl=il+ix+icon.shape[1]+round(3*scale) if kind=='countdown' else x+w+round(3*scale)
                nr=x-round(3*scale) if kind=='countdown' else min(gray.shape[1],x+w+round(90*scale))
                value,confidence=self.read_number(gray[max(0,yy-3):min(gray.shape[0],yy+h+4),nl:nr],scale)
                combined=min(score,icon_score)
                out['countdown_score']=max(out['countdown_score'],combined)
                if value is None:continue
                if combined>out.get('badge_score',0):
                    out.update(countdown=kind=='countdown',confirm=kind=='confirm',kind=kind,value=value,
                               badge_score=combined,digit_confidence=confidence,icon_score=icon_score)
        return out

    @staticmethod
    def edge(region,template):
        if region.size==0 or region.shape[0]<template.shape[0] or region.shape[1]<template.shape[1]:return 0.,(0,0)
        a=cv2.Canny(region,20,60);b=cv2.Canny(template,20,60)
        _,score,_,loc=cv2.minMaxLoc(cv2.matchTemplate(a,b,cv2.TM_CCORR_NORMED))
        return max(0.,float(score)),loc

    def read_number(self,gray,scale):
        if gray.size==0:return None,0.
        _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)
        count,labels,stats,_=cv2.connectedComponentsWithStats(mask)
        chars=[];dots=[]
        for i in range(1,count):
            x,y,w,h,area=map(int,stats[i])
            if 9*scale<=h<=24*scale and 2*scale<=w<=19*scale and area>=9*scale*scale:
                image=cv2.resize(np.where(labels[y:y+h,x:x+w]==i,255,0).astype('uint8'),(20,28)).astype('float32')/255
                by={}
                for label,ref,ratio in self.digits:
                    score=0. if abs(w/h-ratio)>.25 else .55*float(np.sum(image*ref)/(np.linalg.norm(image)*np.linalg.norm(ref)+1e-6))+.45*(1-float(np.mean(abs(image-ref))))
                    by[int(label)]=max(by.get(int(label),0),score)
                ranked=sorted(by.items(),key=lambda a:a[1],reverse=True)
                if ranked[0][1]<.80 or ranked[0][1]-ranked[1][1]<.025:return None,0.
                chars.append((x,ranked[0][0],ranked[0][1],y+h))
            elif 1*scale<=w<=5*scale and 1*scale<=h<=5*scale:dots.append((x,y+h))
        chars.sort()
        if not 2<=len(chars)<=4:return None,0.
        # A decimal point before the last glyph separates tenths from seconds.
        if not any(chars[-2][0]<x<chars[-1][0] and y>=min(c[3] for c in chars)-5*scale for x,y in dots):return None,0.
        value=int(''.join(str(c[1]) for c in chars))/10
        if value>99.9:return None,0.
        return value,min(c[2] for c in chars)

class TransferFlow:
    """Transit context never suppresses death; only a verified fade protects loading."""
    def __init__(self):self.reset()
    def reset(self):
        self.phase='idle';self.started=None;self.saw_countdown=False;self.saw_loading=False
        self.hud_since=None;self.hud_frames=0;self.warned=False;self.last_badge=None
        self.kind=None;self.value=None;self.decreases=0
        self.departed=False;self.depart_since=None;self.depart_frames=0;self.last_frame=None
        self.missed_transition=False;self.manual_notice=False;self.hud_elapsed=0.;self.last_hud_sample=None
        self.return_pending=False;self.return_unobserved=False;self.return_evidence=None
    @property
    def active(self):return self.phase!='idle'
    @property
    def protected(self):return self.saw_loading or self.return_unobserved
    def pause(self):
        # Preserve observed departure, but never count time outside the game as HUD stability.
        observed_now=self.last_frame if self.last_frame is not None else self.last_badge
        pending_transit=(self.last_badge is not None and self.value is not None and observed_now is not None and
                         (observed_now-self.last_badge<=4 or self.last_badge+self.value-observed_now>=-1.5))
        self.hud_since=None;self.hud_frames=0;self.depart_since=None;self.depart_frames=0;self.last_frame=None;self.hud_elapsed=0.;self.last_hud_sample=None
        if self.active and self.kind=='countdown' and not self.saw_loading and pending_transit:self.return_pending=True
        if self.protected and not self.departed:self.missed_transition=True
    def advance_wait(self,now):
        if self.protected and not self.warned and now-self.started>=90:
            self.warned=True;return 'transfer_wait_long'
        return None
    def recent_countdown(self,now):
        return self.kind=='countdown' and self.decreases>=1 and self.last_badge is not None and now-self.last_badge<=4
    def confirm_fade(self,now,evidence):
        if not self.protected and self.recent_countdown(now) and evidence.get('confirmed'):
            self.saw_loading=True;self.phase='leaving_old_map';self.departed=False
            self.hud_since=None;self.hud_frames=0;self.depart_since=None;self.depart_frames=0;self.last_frame=now
            return True
        return False
    def update(self,now,cue,hud,ui_absent=None,ui_visible=None):
        self.last_frame=now
        menu=cue.get('menu',False);badge=cue.get('countdown',False) or cue.get('confirm',False)
        event=self.advance_wait(now)
        if ui_visible is None:ui_visible=hud or menu
        if self.return_unobserved and (badge or menu):self.return_pending=True
        returning=self.return_pending
        previous_deadline=self.last_badge+self.value if self.last_badge is not None and self.value is not None else None
        if self.return_pending and not self.saw_loading:
            if badge or menu:
                self.return_unobserved=False;self.return_pending=False;self.manual_notice=False
            elif ui_absent is True or (previous_deadline is not None and now>=previous_deadline):
                # Only a known transit interrupted by foreground loss reaches this
                # state. Missing observations cannot establish death or loading.
                self.return_unobserved=True;self.return_pending=False;self.phase='return_unobserved'
                self.return_evidence=dict(reason='transition_not_observed',expected_end=previous_deadline)
                event='transfer_return_unobserved'
            elif ui_visible:
                self.return_pending=False
        if self.return_unobserved:
            self.phase='return_unobserved'
            if ui_visible and not self.manual_notice:
                self.manual_notice=True;event='transfer_manual_rearm_needed'
            return dict(phase=self.phase,protected=True,event=event,play_enabled=False,
                        return_evidence=self.return_evidence)
        # A bare confirm/menu hint is not observed loading. Clear it only when
        # a fresh full observation positively shows combat HUD and no badge/page.
        # Genuine countdown/focus-return/loading sequences retain their policy.
        hint_stamp=self.last_badge if self.last_badge is not None else self.started
        if (not self.protected and self.phase in ('confirm','transfer_menu') and
            self.kind!='countdown' and not self.return_pending and not menu and not badge and
            cue.get('badge_observed',False) and hud and (hint_stamp is None or now-hint_stamp>=.75)):
            previous=self.phase;self.reset()
            return dict(phase='idle',protected=False,event='transfer_hint_cancelled',
                        play_enabled=False,return_evidence=dict(previous_phase=previous,reason='fresh_hud_without_transfer_hint'))
        if not self.active and (menu or badge):
            self.started=now;self.phase='transfer_menu' if menu else cue.get('kind','countdown');event='transfer_started'
        if not self.protected:
            if menu:self.phase='transfer_menu'
            if badge:
                kind=cue.get('kind','countdown');value=cue.get('value')
                if kind!=self.kind:self.decreases=0;self.value=None
                elapsed=now-self.last_badge if self.last_badge is not None else None
                observed_gap_decrease=False
                if value is not None:
                    drop=self.value-value if self.value is not None else None
                    elapsed=now-self.last_badge if self.last_badge is not None else None
                    observed_gap_decrease=(returning and drop is not None and drop>.04 and elapsed is not None
                                           and abs(drop-elapsed)<=max(.75,elapsed*.10))
                    if drop is not None and ( .04<drop<5 or observed_gap_decrease):self.decreases+=1
                    elif self.value is not None and value>self.value+.2:self.decreases=0
                    self.value=value
                self.kind=kind;self.last_badge=now;self.phase=kind
                if kind=='countdown' and not self.saw_countdown:event='transfer_countdown'
                elif kind=='countdown' and returning:
                    event='transfer_countdown_reacquired'
                    self.return_evidence=dict(value=value,gap_seconds=elapsed,observed_decrease=observed_gap_decrease)
                self.saw_countdown|=kind=='countdown'
        else:
            if not self.departed:
                if ui_absent is True and not menu and not badge:
                    if self.depart_since is None:self.depart_since=now
                    self.depart_frames+=1
                    if self.depart_frames>=2 and now-self.depart_since>=.10:
                        self.departed=True;self.phase='loading';event='transfer_departure_confirmed'
                        self.hud_since=None;self.hud_frames=0
                else:self.depart_since=None;self.depart_frames=0
                # Missing the whole transition cannot be repaired with a clock alone.
                if self.missed_transition and hud:
                    if self.hud_since is None:self.hud_since=now
                    if now-self.hud_since>=1.0 and not self.manual_notice:
                        self.manual_notice=True;event='transfer_manual_rearm_needed'
                else:self.hud_since=None
            elif hud and not menu and not badge and cue.get('badge_observed',False):
                if self.hud_since is None:self.hud_since=now
                if self.last_hud_sample is not None:self.hud_elapsed+=min(max(0.,now-self.last_hud_sample),.25)
                self.last_hud_sample=now;self.hud_frames+=1;self.phase='reacquiring_hud'
                if self.hud_frames>=2 and self.hud_elapsed>=.2:
                    self.reset();return dict(phase='idle',protected=False,event='transfer_resumed',play_enabled=True)
            elif not hud or menu or badge:
                self.hud_since=None;self.hud_frames=0;self.hud_elapsed=0.;self.last_hud_sample=None;self.phase='loading'
        return dict(phase=self.phase,protected=self.protected,event=event,play_enabled=False,
                    return_evidence=self.return_evidence)

def resume_hud(features):
    return features.get('combat_score',0)>=.48 and features.get('bar_frames',0)>=.22 and features.get('label_shape',0)>=.35
