"""Per-clip volume strip in the PSD's reserved space; no layout reflow."""
from PIL import Image

class VolumeControl:
    def setup_volume(self):
        self.volume_choice=self.app.sounds.selected['exit']
        self.volume_started=None;self.volume_deadline=None
        self.volume_held=False;self.volume_drag=False;self.volume_visible=False
        self.canvas.bind('<B1-Motion>',self.volume_motion)

    def show_volume(self):
        now=self.clock()
        if not self.volume_visible:self.volume_started=now
        self.volume_deadline=now+1.59;self.schedule()

    def volume_pointer(self,e):
        x,y=self.coords(e)
        inside=(250<=x<=665 and 508<=y<=572) or (self.volume_visible and 140<=x<=704 and 573<=y<=610)
        inside=inside and not self.dropdown and not self.dropdown_paint and bool(self.app.sounds.selected['exit'])
        if inside and not self.volume_held:self.show_volume()
        if self.volume_held and not inside:self.volume_deadline=self.clock()+1.5
        self.volume_held=inside

    def volume_motion(self,e):
        if self.volume_drag:self.set_volume_at(e)
        else:self.motion(e)

    def set_volume_at(self,e):
        choice=self.app.sounds.selected['exit']
        value=max(0,min(100,round((self.coords(e)[0]-260)/428*100)))
        if choice and self.app.sounds.volume(choice)!=value:
            self.app.sounds.set_volume(choice,value)
            self.app.prep_feedback.set('尚未保存')
        self.show_volume()

    def draw_volume(self,im,muted):
        choice=self.app.sounds.selected['exit'];now=self.last_now
        if choice!=self.volume_choice:
            self.volume_choice=choice
            if choice:self.show_volume()
        if self.volume_started is None or not choice:
            self.volume_visible=False;return
        held=self.volume_held or self.volume_drag
        age=now-self.volume_started
        deadline=self.volume_deadline or now
        alpha=self.notice_alpha(age) if age<.09 else (1. if held or now<deadline else self.notice_alpha(3.09+now-deadline))
        if alpha<=0 and age>=.09:
            self.volume_started=None;self.volume_visible=False;return
        self.volume_visible=True
        if age<.09 or (not held and now>=deadline):self.moving=True
        elif not held:self.wake_at=min(self.wake_at or deadline,deadline)
        if self.dropdown or self.dropdown_paint:return
        dy=round(-5*(1-min(1,age/.09))**2)
        self.paste(im,self.colored(self.tile('volume_label'),muted),146,583+dy,alpha)
        self.paste(im,self.text_tile(str(self.app.sounds.volume(choice))+'%',18,muted,en=True),199,585+dy,alpha)
        track=self.tile('volume_track');fill=self.tile('volume_fill');knob=self.tile('volume_knob')
        if self.theme=='light':
            track=self.colored(track,'#AAA5B0');fill=self.colored(fill,'#C4BFCC');knob=self.colored(knob,'#77717D')
        self.paste(im,track,250,585+dy,alpha);self.paste(im,fill,252,586+dy,alpha)
        self.paste(im,knob,250+428*self.app.sounds.volume(choice)/100,581+dy,alpha)
        self.control('volume_slider',(246,576,703,607),self.volume_keyboard)

    def volume_keyboard(self):
        self.show_volume()

    def volume_key(self,e):
        if self.focus_key!='volume_slider' or e.keysym not in ('Left','Right','Home','End'):return
        choice=self.app.sounds.selected['exit'];current=self.app.sounds.volume(choice)
        value={'Left':current-1,'Right':current+1,'Home':0,'End':100}[e.keysym]
        self.app.sounds.set_volume(choice,value);self.app.prep_feedback.set('尚未保存');self.show_volume();return 'break'
