"""Fresh full-screen evidence for Steam overlay and movable BTR dialogue.

Only anonymous toolbar glyphs and the NPC portrait/header are stored as assets.
No keyboard, process, network, OCR or previous-frame immunity.
"""
from pathlib import Path
import cv2
import numpy as np


class InteractionUI:
    def __init__(self,assets=None):
        root=Path(assets or Path(__file__).parent/'assets')/'interaction'
        self.templates={p.stem:cv2.imdecode(np.frombuffer(p.read_bytes(),np.uint8),0)
                        for p in root.glob('*.png')}
        self.steam={name:cv2.Canny(image,35,90) for name,image in self.templates.items()
                    if name.startswith('steam_')}
        self._scaled_cache={}

    def _scaled(self,name,scale,nearest=False):
        key=(name,round(float(scale),4),nearest)
        if key not in self._scaled_cache:
            base=self.steam.get(name) if nearest else self.templates.get(name)
            if base is None:return None
            self._scaled_cache[key]=base if abs(scale-1.)<1e-6 else cv2.resize(
                base,None,fx=scale,fy=scale,interpolation=cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR)
        return self._scaled_cache[key]

    @staticmethod
    def match(image,template):
        if image.shape[0]<template.shape[0] or image.shape[1]<template.shape[1]:return 0.,(0,0)
        _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(image,template,cv2.TM_CCOEFF_NORMED))
        return float(score),point

    def steam_windows(self,gray):
        """Movable Steam-specific compound chrome, never a generic window alone."""
        h,w=gray.shape
        # Cheap, non-authoritative prefilter. It can only avoid expensive
        # template searches; a rectangle never establishes Steam identity.
        preview=cv2.resize(gray,(480,max(1,round(h*480/w))))
        gradient=cv2.morphologyEx(preview,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8))
        flat=((gradient<=5)&(preview>=12)&(preview<=245)).astype('uint8')*255
        flat=cv2.morphologyEx(flat,cv2.MORPH_CLOSE,np.ones((3,9),np.uint8))
        window_possible=False
        for contour in cv2.findContours(flat,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            x,y,bw,bh=cv2.boundingRect(contour)
            touched=sum((x==0,y==0,x+bw>=preview.shape[1],y+bh>=preview.shape[0]))
            if bw>=45 and bh>=4 and bw*bh>=300 and touched<3 and cv2.contourArea(contour)/(bw*bh)>=.40:
                window_possible=True;break
        if not window_possible:return dict(steam=False)
        return_ref=self.templates.get('overlay_return')
        if return_ref is not None:
            for factor in (.8,1.,1.2):
                ref=self._scaled('overlay_return',factor)
                score,point=self.match(gray[:round(h*.16),round(w*.55):],ref)
                if score>=.86:return dict(steam=True,steam_source='return_hotkey_and_close',steam_score=round(score,3))
        # Match small compound chrome at half resolution, then check the
        # independently located pin/close controls in the same title bar.
        small=cv2.resize(gray,None,fx=.5,fy=.5)
        nav=self.templates.get('overlay_browser_nav');buttons=self.templates.get('overlay_pin_close')
        if nav is not None and buttons is not None:
            for factor in (.8,1.,1.2):
                ref=self._scaled('overlay_browser_nav',factor*.5)
                score,(x,y)=self.match(small,ref)
                if score<.83:continue
                x*=2;y*=2
                box=gray[max(0,y-round(35*factor)):max(0,y-round(5*factor)),
                         min(w,x+round(180*factor)):]
                control=self._scaled('overlay_pin_close',factor)
                controls,_=self.match(box,control)
                if controls>=.80:return dict(steam=True,steam_source='browser_navigation_and_pin_close',steam_score=round(min(score,controls),3))
        # Friend header uses a search + person/add pair on a flat strip. The
        # strip, dark panel beneath and compound glyph are all required.
        friend=self.templates.get('overlay_friend_tools')
        if friend is not None:
            for factor in (.8,1.,1.2):
                ref=self._scaled('overlay_friend_tools',factor*.5)
                score,(x,y)=self.match(small,ref)
                if score<.84:continue
                x*=2;y*=2
                strip=gray[max(0,y-round(3*factor)):y+round(20*factor),max(0,x-round(150*factor)):x]
                body=gray[y+round(30*factor):min(h,y+round(110*factor)),max(0,x-round(150*factor)):x+round(30*factor)]
                if strip.size and body.size and float(np.mean(abs(strip.astype('float32')-np.median(strip))<5))>.85 and float(np.median(body))<float(np.median(strip))-5:
                    return dict(steam=True,steam_source='friend_compound_header_and_panel',steam_score=round(score,3))
        return self.steam_widgets(gray,small)

    def steam_widgets(self,gray,small=None):
        """Independent identities for overview, achievements, settings and chrome."""
        h,w=gray.shape
        if small is None:small=cv2.resize(gray,None,fx=.5,fy=.5)
        def locate(name,factor,region=small):
            ref=self.templates.get(name)
            if ref is None:return 0.,(0,0)
            score,(x,y)=self.match(region,self._scaled(name,.5*factor))
            if region is small and score>=.55:
                full=self._scaled(name,factor)
                left=max(0,2*x-4);top=max(0,2*y-4)
                patch=gray[top:min(h,2*y+full.shape[0]+5),left:min(w,2*x+full.shape[1]+5)]
                refined,(dx,dy)=self.match(patch,full)
                if refined>score:return refined,(round((left+dx)/2),round((top+dy)/2))
            return score,(x,y)
        for factor in (.8,1.,1.2):
            # The return label and large overlay X may remain when Shift+Tab
            # text is covered by a draggable friend window.
            score,(x,y)=locate('overlay_return_words',factor,small[:round(h*.08),round(w*.275):])
            if score>=.88:
                x=2*x+round(w*.55);y*=2
                patch=gray[max(0,y-round(15*factor)):min(h,y+round(45*factor)),
                           min(w,x+round(55*factor)):min(w,x+round(130*factor))]
                ref=self.templates.get('overlay_return_x')
                control,_=self.match(patch,cv2.resize(ref,None,fx=factor,fy=factor))
                if control>=.82:return dict(steam=True,steam_source='return_words_and_overlay_close')
            score,(x,y)=locate('overlay_overview_title',factor)
            if score>=.87:
                x*=2;y*=2
                patch=gray[y+round(20*factor):min(h,y+round(550*factor)),max(0,x-round(3*factor)):min(w,x+round(360*factor))]
                # Repeated flat card headings below the unique overview title.
                bars=[]
                if patch.shape[1]>=120*factor:
                    levels=np.median(patch,axis=1)
                    flat=np.mean(abs(patch.astype('float32')-levels[:,None])<6,axis=1)>=.65
                    start=0
                    for row in range(1,len(levels)+1):
                        if row==len(levels) or not flat[row] or not flat[row-1] or abs(float(levels[row])-float(levels[row-1]))>5:
                            if flat[start] and 20<=levels[start]<=235 and 12*factor<=row-start<=32*factor:bars.append((start,row))
                            start=row
                if len(bars)>=2:return dict(steam=True,steam_source='overview_title_and_widget_cards')
                # Another Steam window may cover all the cards while the
                # overview's own caption and close control remain exposed.
                buttons=self.templates.get('overlay_pin_close')
                if buttons is not None:
                    close=self.templates.get('overlay_overview_close',buttons[:,-14:])
                    close=cv2.resize(close,None,fx=factor,fy=factor)
                    strip=gray[max(0,y-round(35*factor)):max(0,y-round(2*factor)),
                               min(w,x+round(70*factor)):min(w,x+round(500*factor))]
                    control,_=self.match(strip,close)
                    if control>=.83:return dict(steam=True,steam_source='overview_identity_and_own_close')
            for suffix in ('','_capture'):
                score,(x,y)=locate('overlay_achievement_title'+suffix,factor)
                if score>=.86:
                    x*=2;y*=2
                    patch=gray[max(0,y+round(25*factor)):min(h,y+round(140*factor)),max(0,x-round(70*factor)):min(w,x+round(600*factor))]
                    ref=self.templates.get('overlay_achievement_controls'+suffix)
                    controls,_=self.match(patch,cv2.resize(ref,None,fx=factor,fy=factor))
                    if controls>=.82:return dict(steam=True,steam_source='achievement_title_and_statistics_controls')
            score,(x,y)=locate('overlay_settings_title',factor)
            if score>=.87:
                x*=2;y*=2
                patch=gray[y+round(20*factor):min(h,y+round(180*factor)),max(0,x-round(8*factor)):min(w,x+round(110*factor))]
                ref=self.templates.get('overlay_settings_nav')
                nav,_=self.match(patch,cv2.resize(ref,None,fx=factor,fy=factor))
                if nav>=.82:return dict(steam=True,steam_source='steam_settings_identity_and_navigation')
            # Steam windows expose a pin and close control pair; EFT item
            # inspection windows have a red close button and no pin.
            score,(x,y)=locate('overlay_pin_close',factor)
            if score>=.91:
                x*=2;y*=2
                strip=gray[max(0,y-round(6*factor)):min(h,y+round(14*factor)),max(0,x-round(180*factor)):x]
                if strip.shape[1]>=100 and float(np.mean(abs(strip.astype('float32')-np.median(strip))<6))>.85:
                    level=float(np.median(strip))
                    if 20<=level<=235:return dict(steam=True,steam_source='pinned_window_controls_and_caption_strip')
        return dict(steam=False)

    @staticmethod
    def btr_scales(image,layout=None):
        height,width=image.shape[:2]
        if layout:
            scale=1080/layout['height']
            active_width=(layout['right']-layout['left'])*scale
            active_height=(layout['height']-2*layout['top'])*scale
        else:
            # Full screenshots may contain a narrower game viewport. Ignore
            # FPS text at the very top and the dialogue at the bottom.
            black=np.mean(cv2.inRange(image[40:max(41,round(height*.75))],(0,0,0),(8,8,8))>0,axis=0)>=.97
            left=0;right=width
            while left<width//3 and black[left]:left+=1
            while right>width*2//3 and black[right-1]:right-=1
            active_width=right-left;active_height=height
        predicted=min(active_width/1920,active_height/1080)
        # Start with the measured scale, retaining the old fallback sizes.
        # Fine neighbours handle UI scaling/capture rounding without lowering
        # portrait, title or panel requirements.
        values=[predicted,1.,.8,1.2,predicted-.05,predicted+.05,predicted-.025,predicted+.025]
        return list(dict.fromkeys(round(v,3) for v in values if .55<=v<=1.5))

    def _detect_btr(self,image,gray,layout=None):
        if 'btr_face' not in self.templates:return None
        height,width=gray.shape
        y0=round(height*.35);x0=max(0,round(width*.08));x1=min(width,round(width*.88))
        region=cv2.resize(gray[y0:,x0:x1],None,fx=.5,fy=.5)
        scales=self.btr_scales(image,layout)
        primary=scales[:3];fine=scales[3:]
        best_primary=0.
        for idx,factor in enumerate(primary):
            face=self._scaled('btr_face',factor)
            score,(x,y)=self.match(region,cv2.resize(face,None,fx=.5,fy=.5))
            if idx==0:best_primary=score
            if score<.76:continue
            x=2*x+x0;y=2*y+y0
            hx=x+round(100*factor);hy=y-round(8*factor)
            header=gray[max(0,hy-8):min(height,hy+round(34*factor)),max(0,hx-10):min(width,hx+round(65*factor))]
            name=self._scaled('btr_name',factor)
            name_score,_=self.match(header,name)
            px=x+round(80*factor);py=max(0,y-round(8*factor))
            panel=gray[py:min(height,py+round(210*factor)),px:min(width,px+round(780*factor))]
            if panel.shape[0]<80 or panel.shape[1]<200:continue
            dark=float(np.mean(panel<55));ink=float(np.mean(panel>140))
            if name_score>=.72 and dark>=.55 and .008<=ink<=.35:
                return dict(btr=True,btr_scale=factor,btr_face=round(score,3),btr_header=round(name_score,3),
                            btr_panel=[px,py,px+panel.shape[1],py+panel.shape[0]])
        if best_primary<.52:return None
        for factor in fine:
            face=self._scaled('btr_face',factor)
            score,(x,y)=self.match(region,cv2.resize(face,None,fx=.5,fy=.5))
            if score<.76:continue
            x=2*x+x0;y=2*y+y0
            hx=x+round(100*factor);hy=y-round(8*factor)
            header=gray[max(0,hy-8):min(height,hy+round(34*factor)),max(0,hx-10):min(width,hx+round(65*factor))]
            name=self._scaled('btr_name',factor)
            name_score,_=self.match(header,name)
            px=x+round(80*factor);py=max(0,y-round(8*factor))
            panel=gray[py:min(height,py+round(210*factor)),px:min(width,px+round(780*factor))]
            if panel.shape[0]<80 or panel.shape[1]<200:continue
            dark=float(np.mean(panel<55));ink=float(np.mean(panel>140))
            if name_score>=.72 and dark>=.55 and .008<=ink<=.35:
                return dict(btr=True,btr_scale=factor,btr_face=round(score,3),btr_header=round(name_score,3),
                            btr_panel=[px,py,px+panel.shape[1],py+panel.shape[0]])
        return None

    def detect(self,frame,full_size,layout=None,steam_only=False):
        fw,fh=full_size
        out=dict(observed=False,steam=False,btr=False)
        if frame is None or frame.shape[0]<fh-2:return out
        out['observed']=True
        scale=1080/fh
        image=cv2.resize(frame[:,:,:3],None,fx=scale,fy=scale)
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);height,width=gray.shape
        if not steam_only:
            btr=self._detect_btr(image,gray,layout)
            if btr:
                out.update(btr);return out
        window=self.steam_windows(gray)
        if window.get('steam'):
            out.update(window);return out
        # Overlay toolbar is height-scaled and centered, independent of viewport.
        x0=max(0,width//2-570);x1=min(width,width//2+570);y0=round(height*.87)
        toolbar=image[y0:round(height*.995),x0:x1]
        tile_mask=cv2.inRange(toolbar,(20,20,20),(190,190,190))
        tiles=[]
        for contour in cv2.findContours(tile_mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
            x,y,w,h=cv2.boundingRect(contour)
            if 16<=w<=50 and 16<=h<=50 and .8<=w/h<=1.25 and cv2.contourArea(contour)/(w*h)>=.65:
                tiles.append((x,y,w,h))
        toolbar_possible=any(len([b for b in tiles if abs(b[1]-a[1])<=5])>=5 for a in tiles)
        edges=cv2.Canny(gray[y0:round(height*.995),x0:x1],35,90) if toolbar_possible else None
        for factor in ((.75,1.,1.25) if toolbar_possible else ()):
            hits=[]
            for name in self.steam:
                ref=self._scaled(name,factor,nearest=True)
                score,(x,y)=self.match(edges,ref)
                if score>=.72:hits.append((name,x,y,score))
            # Several distinct icons, on the same baseline and a broad toolbar.
            for _,_,row,_ in hits:
                aligned=[v for v in hits if abs(v[2]-row)<=6*factor]
                if len(aligned)<4:continue
                xs=sorted(v[1] for v in aligned)
                if min(b-a for a,b in zip(xs,xs[1:]))<18*factor or xs[-1]-xs[0]<110*factor:continue
                out.update(steam=True,steam_icons=[v[0] for v in aligned],steam_scale=factor)
                return out
        return out


class SteamOverlayState:
    """Known overlay ambiguity remains visibly paused, never a death or stop."""
    def __init__(self):
        self.paused=False;self.unknown_since=None;self.game_frames=0;self.last_game_frame=None

    def update(self,now,recognized,game_visible):
        if recognized:
            entered=not self.paused;self.paused=True;self.unknown_since=None;self.game_frames=0;self.last_game_frame=None
            return 'paused_new' if entered else 'paused'
        if not self.paused:return 'game'
        if game_visible:
            if self.last_game_frame is None or now>self.last_game_frame:
                self.game_frames+=1;self.last_game_frame=now
            if self.game_frames<2:return 'waiting'
            self.paused=False;self.unknown_since=None;return 'resumed'
        self.game_frames=0;self.last_game_frame=None
        if self.unknown_since is None:self.unknown_since=now
        return 'waiting'


def confirmed_game_ui(features,menu_features):
    hud=(features.get('combat_score',0)>=.48 and features.get('bar_frames',0)>=.22
         and features.get('label_shape',0)>=.35)
    hud=hud or any(features.get(key,0)>=.8 for key in ('body_shape','bar_frames','label_shape'))
    return bool(hud or menu_features.get('inventory_ui',{}).get('present')
                or menu_features.get('settings',0)>=.8 or menu_features.get('pause',0)>=.8
                or menu_features.get('esc_title',{}).get('present')
                or menu_features.get('interaction_ui',{}).get('btr'))
