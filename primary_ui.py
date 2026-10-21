"""PSD V2 pixels with stable controls and event-driven animation; primary only."""
from pathlib import Path
from dataclasses import dataclass
import json,time,math,tkinter as tk
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageTk,ImageChops,ImageColor

def ease(t):
    t=max(0.,min(1.,t));return t*t*(3-2*t)
def mix(a,b,t):return tuple(round(x+(y-x)*t) for x,y in zip(a,b))
@dataclass
class Control:
    rect:tuple
    action:object=None
    enabled:bool=True

from dropdowns import Dropdowns

class PrimaryUI(Dropdowns):
    def __init__(self,app,assets):
        self.app=app;self.assets=Path(assets);self.art=self.assets/'ui_v2'
        self.meta=json.loads((self.art/'manifest.json').read_text(encoding='utf-8'))
        self.theme=app.ui_theme;self.clock=time.monotonic;self.width=1320;self.height=1758
        self.fonts={};self.tiles={};self.pending=False;self.timer=None;self.image=None
        self.focus_key=None;self.focus_visible=False;self.hover_key=None;self.pressed_key=None
        self.controls={};self.hits=[];self.tweens={};self.values={};self.pulses={};self.notices={}
        self.last_now=self.clock();self.moving=False;self.wake_at=None;self.scale=.7;self.offset=(0,0)
        self.reference=Image.open(self.art/'reference.png').convert('RGB')
        self.card=self.pixel(self.reference,500,680);self.field=self.pixel(self.reference,1000,520)
        self.dark=self.reference.copy();d=ImageDraw.Draw(self.dark)
        for r in ((142,667,418,701),(140,1362,444,1396)):self.fill(d,r,self.card)
        self.light=self.lighten(self.dark)
        self.canvas=tk.Canvas(app.root,highlightthickness=0,bd=0,bg='#111115',takefocus=True);self.canvas.pack(fill='both',expand=True)
        self.canvas_item=self.canvas.create_image(0,0,anchor='nw')
        self.setup_dropdowns()
        self.previous_source=None;self.display_pil=None;self.previous_moving=False;self.previous_theme=None;self.border_cache={}
        for event,fn in [('<Configure>',lambda e:self.schedule()),('<Button-1>',self.mouse_down),('<ButtonRelease-1>',self.mouse_up),('<Motion>',self.motion),('<Leave>',self.leave),('<Tab>',self.tab),('<Shift-Tab>',lambda e:self.tab(e,-1)),('<Return>',self.activate),('<space>',self.activate),('<FocusOut>',self.focus_out)]:self.canvas.bind(event,fn)
        for v in (app.status,app.bottom_status,app.state_head,app.key_label,app.dry,app.return_sound,app.mute_var,app.sound_on_var,app.sound_off_var,app.delay_text,app.hz_text,app.collect_var,app.log_var):v.trace_add('write',lambda *args:self.schedule())
        for k,v in (('prep',app.prep_feedback),('advanced',app.advanced_feedback)):v.trace_add('write',lambda *args,k=k,v=v:self.notice_changed(k,v))
        s=min(.72,(app.root.winfo_screenwidth()-100)/1320,(app.root.winfo_screenheight()-130)/1758)
        app.root.geometry(f'{round(1320*s)}x{round(1758*s)}');app.root.minsize(round(1320*min(s,.42)),round(1758*min(s,.42)));app.root.resizable(True,True);self.schedule()

    @staticmethod
    def pixel(im,x,y):return im.getpixel((x-60,y-81))
    @staticmethod
    def local(r):return (r[0]-60,r[1]-81,r[2]-60,r[3]-81)
    @classmethod
    def fill(cls,d,r,color):
        x0,y0,x1,y1=cls.local(r);d.rectangle((x0,y0,x1-1,y1-1),fill=color)
    def lighten(self,im):
        a=np.array(im);rgb=a.astype('int16');neutral=(rgb.max(2)-rgb.min(2))<16
        v=np.interp(rgb.mean(2),[0,17,24,32,59,68,153,255],[255,244,255,247,227,213,119,36]);a[neutral]=np.repeat(v[:,:,None],3,2)[neutral].astype('uint8')
        out=Image.fromarray(a);r=self.meta['avatar']['bbox'];self.fill(ImageDraw.Draw(out),r,(255,255,255));tile=self.tile('avatar');out.paste(tile,(r[0]-60,r[1]-81),tile);return out
    def tile(self,name):
        if name not in self.tiles:self.tiles[name]=Image.open(self.art/(name+'.png')).convert('RGBA')
        return self.tiles[name]
    def font(self,name,size):
        if (name,size) not in self.fonts:self.fonts[name,size]=ImageFont.truetype(str(self.assets/'fonts'/name),size)
        return self.fonts[name,size]
    def text_tile(self,text,size,color,en=False,maxwidth=None):
        key=('text',text,size,color,en,maxwidth)
        if key in self.tiles:return self.tiles[key]
        name='Arvo-Bold.ttf' if en else 'Plix-Regular.otf'
        while maxwidth and size>10 and self.font(name,size).getlength(text)*(1 if en else 1.1)>maxwidth:size-=1
        f=self.font(name,size*2);im=Image.new('RGBA',(max(1,round(f.getlength(text)))+12,size*4+12))
        ImageDraw.Draw(im).text((4,4),text,font=f,fill=color,anchor='lt',stroke_width=0 if en else 1)
        if im.getbbox():im=im.crop(im.getbbox())
        im=im.resize((max(1,round(im.width*(.5 if en else .55))),max(1,round(im.height*.5))),Image.Resampling.LANCZOS)
        if len(self.tiles)>180:
            for k in list(self.tiles):
                if isinstance(k,tuple):del self.tiles[k]
        self.tiles[key]=im;return im
    def mode_caption(self,color):
        return self.colored(self.tile('actual_mode_caption'),color)

    @staticmethod
    def colored(tile,color):
        result=Image.new('RGBA',tile.size,color);result.putalpha(tile.getchannel('A'));return result
    def paste(self,im,tile,x,y,alpha=1.):
        if alpha<=0:return
        if alpha<1:tile=tile.copy();tile.putalpha(tile.getchannel('A').point(lambda v:round(v*alpha)))
        im.paste(tile,(round(x-60),round(y-81)),tile)
    def centered(self,im,tile,r):
        if tile.getbbox():tile=tile.crop(tile.getbbox())
        x=round((r[0]+r[2]-tile.width)/2);y=round((r[1]+r[3]-tile.height)/2)
        self.centered_bounds.append((r,(x,y,x+tile.width,y+tile.height)))
        self.paste(im,tile,x,y)
    def schedule(self):
        if self.timer is not None:self.app.root.after_cancel(self.timer);self.timer=None
        if not self.pending:self.pending=True;self.app.root.after_idle(self.render)
    def sample(self,key,now):
        if key not in self.tweens:return self.values.get(key,0.)
        start,target,when,duration=self.tweens[key];t=(now-when)/duration;value=start+(target-start)*ease(t)
        if t>=1:del self.tweens[key];self.values[key]=target;return target
        self.moving=True;return value
    def tween(self,key,target,duration=.18):
        if key not in self.values:self.values[key]=target
        value=self.sample(key,self.last_now);old_target=self.tweens[key][1] if key in self.tweens else self.values[key]
        if target!=old_target:self.tweens[key]=(value,target,self.last_now,duration)
        return self.sample(key,self.last_now)
    def notice_changed(self,key,var):
        if var.get():self.notices[key]=(var.get(),self.clock())
        else:self.notices.pop(key,None)
        self.schedule()
    @staticmethod
    def notice_alpha(age):
        if age<0:return 0.
        if age<.09:return ease(age/.09)
        if age<3.09:return 1.
        if age<3.54:return 1-ease((age-3.09)/.45)
        return 0.
    def palette(self):
        return dict(bg='#111115' if self.theme=='dark' else '#F4F3F7',card='#18181C' if self.theme=='dark' else '#FFFFFF',text='#FFFFFF' if self.theme=='dark' else '#24222A',muted='#999999' if self.theme=='dark' else '#77717D',accent='#A44C50')
    def control(self,key,rect,action=None,enabled=True):self.controls[key]=Control(rect,action,enabled);self.hits.append((rect,action))

    def render(self):
        frame_started=time.perf_counter()
        self.pending=False
        if not self.canvas.winfo_exists():return
        if self.timer is not None:self.app.root.after_cancel(self.timer);self.timer=None
        self.last_now=self.clock();self.moving=False;self.wake_at=None;self.controls={};self.hits=[];self.centered_bounds=[]
        theme=self.tween('theme',0. if self.theme=='dark' else 1.,.22)
        im=self.dark.copy() if theme==0 else self.light.copy() if theme==1 else Image.blend(self.dark,self.light,theme)
        card=mix(self.card,(255,255,255),theme);field=mix(self.field,(247,245,249),theme);fg=mix((255,255,255),(36,34,42),theme);muted=mix((153,153,153),(119,113,125),theme);d=ImageDraw.Draw(im)
        self.control('theme_dark',(1190,173,1261,206),lambda:self.change_theme('dark'));self.control('theme_light',(1262,173,1328,206),lambda:self.change_theme('light'))
        if theme>.001:
            self.fill(d,(1192,175,1261,204),mix((59,59,68),(247,245,249),theme));self.fill(d,(1263,175,1326,204),mix((32,32,36),(227,223,235),theme))
            for key in ('theme_dark','theme_light'):
                r=self.meta[key]['bbox'];self.paste(im,self.colored(self.tile(key),fg),r[0],r[1])
        self.fill(d,(143,462,253,494),card)
        self.paste(im,self.text_tile('大退音效',24,fg),143,465)
        self.draw_segment(im,'preview',(143,509,253,572),self.preview,theme);self.draw_segment(im,'check',(730,508,943,572),self.check_hotkey,theme)
        for name,r,default,value in [('audio_value',(250,508,665,572),'原版死亡回归',self.app.sounds.selected['exit'] or '无音效'),('hotkey_value',(940,508,1253,572),'F10',self.app.key_name),('delay_value',(828,1077,920,1138),'300',self.app.delay_text.get()),('hz_value',(1180,1077,1255,1138),'30',self.app.hz_text.get())]:
            self.fill(d,(r[0]+2,r[1]+3,r[2]-2,r[3]-3),field)
            tile=self.dropdown_text('RE0死亡回归(默认)' if name=='audio_value' and value=='原版死亡回归' else value,muted,name!='audio_value' and value!='未设置',r[2]-r[0]-(75 if name in ('audio_value','hotkey_value') else 20))
            if theme>0:tile=self.colored(tile,muted)
            if name in ('audio_value','hotkey_value'):
                self.paste(im,tile,r[0]+24,(r[1]+r[3]-tile.height)/2)
                self.control(name,r,lambda k='audio' if name=='audio_value' else 'key':self.toggle_dropdown(k))
                if name=='audio_value' and self.app.sounds.selected['exit']:
                    icon=self.colored(self.bin_image,'#AD3535');icon.thumbnail((24,24));self.paste(im,icon,r[2]-36,(r[1]+r[3]-icon.height)/2)
                    self.control('delete_selected',(r[2]-45,r[1],r[2],r[3]),lambda:self.delete_exit(self.app.sounds.selected['exit']))
                if name=='hotkey_value' and self.app.hotkeys:
                    icon=self.colored(self.bin_image,'#AD3535');icon.thumbnail((24,24));self.paste(im,icon,r[2]-36,(r[1]+r[3]-icon.height)/2)
                    self.control('delete_selected_key',(r[2]-45,r[1],r[2],r[3]),lambda:self.delete_key(self.app.hotkey_service.combo))
            else:
                self.centered(im,tile,r);self.control(name,r,lambda k='delay' if name=='delay_value' else 'hz':self.toggle_dropdown(k))
        for key,r,kind in [('audio_arrow',(665,508,699,572),'audio'),('key_arrow',(1253,508,1286,572),'key'),('delay_arrow',(920,1077,951,1138),'delay'),('hz_arrow',(1255,1077,1286,1138),'hz')]:
            self.control(key,r,lambda k=kind:self.toggle_dropdown(k))
        self.draw_save(im,'save_prep',(1058,651,1285,708),self.app.save_preparation,theme);self.draw_save(im,'save_advanced',(1059,1344,1285,1401),self.app.save_advanced,theme)
        mute=self.tween('disabled_audio',float(self.app.mute_var.get()))
        for key,var in [('sound_on',self.app.sound_on_var),('sound_off',self.app.sound_off_var),('sound_exit',self.app.return_sound),('mute',self.app.mute_var),('screenshots',self.app.collect_var),('logs',self.app.log_var),('dry',self.app.dry)]:
            disabled=mute if key in ('sound_on','sound_off','sound_exit') else 0.;enabled=not (key in ('sound_on','sound_off','sound_exit') and self.app.mute_var.get())
            self.draw_switch(im,key,var,theme,disabled,enabled)
        if theme<.01:
            for key in ('on_label','off_label','exit_label'):
                r=self.meta[key]['bbox'];tile=self.tile(key).convert('RGB')
                if mute:tile=Image.blend(tile,Image.new('RGB',tile.size,card),mute*.5)
                im.paste(tile,(r[0]-60,r[1]-81))
        self.draw_notices(im,card,muted);self.draw_status(im,card,fg,muted)
        for key,r,title,fn in [('open_log',(140,1770,290,1815),'打开日志',self.app.open_log),('stop',(1100,1770,1300,1815),'停止监控',lambda:self.app.cancel('手动停止'))]:
            self.control(key,r,fn);h=self.tween('hover:'+key,float(self.hover_key==key),.12);self.centered(im,self.text_tile(title,21,mix(muted,fg,h)),r)
        if self.dropdown or self.dropdown_paint:
            self.draw_dropdown(im,theme)
        if self.dropdown:
            ar={'audio':(665,508,699,572),'key':(1253,508,1286,572),'delay':(920,1077,951,1138),'hz':(1255,1077,1286,1138)}[self.dropdown]
            self.fill(ImageDraw.Draw(im),(ar[0]+3,ar[1]+4,ar[2]-3,ar[3]-4),(255,255,255))
            cx=(ar[0]+ar[2])//2;cy=(ar[1]+ar[3])//2
            ImageDraw.Draw(im).polygon([(cx-7-60,cy+5-81),(cx+7-60,cy+5-81),(cx-60,cy-7-81)],fill=(32,32,36))
        if self.focus_visible and self.focus_key in self.controls:
            c=self.controls[self.focus_key]
            if c.enabled and c.action:ImageDraw.Draw(im).rounded_rectangle(self.local(c.rect),radius=5,outline=fg,width=2)
        self.source_image=im;cw=max(1,self.canvas.winfo_width());ch=max(1,self.canvas.winfo_height());self.scale=min(cw/1320,ch/1758)
        w=max(1,round(1320*self.scale));h=max(1,round(1758*self.scale));self.offset=((cw-w)//2,(ch-h)//2)
        self.present(im,w,h,theme)
        if self.moving:self.timer=self.app.root.after(max(1,round(1000/60-(time.perf_counter()-frame_started)*1000)),self.schedule)
        elif self.wake_at is not None:self.timer=self.app.root.after(max(1,round((self.wake_at-self.last_now)*1000)),self.schedule)

    def present(self,im,w,h,theme):
        """Reuse the Tk image/item; resample only changed UI regions at rest/theme."""
        resized=self.display_pil is None or self.display_pil.size!=(w,h)
        theme_changed=self.previous_theme!=theme
        if resized or theme_changed or self.previous_source is None:
            self.display_pil=im.resize((w,h),Image.Resampling.LANCZOS)
            if resized:
                self.image=ImageTk.PhotoImage(self.display_pil)
                self.canvas.itemconfigure(self.canvas_item,image=self.image)
                self.canvas.coords(self.canvas_item,*self.offset)
            else:self.image.paste(self.display_pil)
            self.canvas.configure(bg=self.palette()['bg'])
        else:
            regions=[c.rect for c in self.controls.values()]
            regions += [(250,508,700,900),(940,508,1287,845),(825,1077,952,1305),(1178,1077,1287,1305)]
            regions += [(138,664,1036,705),(138,1358,1036,1403),(96,1479,1331,1739)]
            regions += [self.meta[k]['bbox'] for k in ('on_label','off_label','exit_label')]
            changed=False;settling=self.previous_moving and not self.moving
            for r in regions:
                x0,y0,x1,y1=self.local(r);x0=max(0,x0-3);y0=max(0,y0-3);x1=min(1320,x1+3);y1=min(1758,y1+3)
                if not settling and ImageChops.difference(im.crop((x0,y0,x1,y1)),self.previous_source.crop((x0,y0,x1,y1))).getbbox() is None:continue
                a=max(0,math.floor(x0*w/1320)-2);b=max(0,math.floor(y0*h/1758)-2)
                c=min(w,math.ceil(x1*w/1320)+2);d=min(h,math.ceil(y1*h/1758)+2)
                patch=im.resize((c-a,d-b),Image.Resampling.LANCZOS,box=(a*1320/w,b*1758/h,c*1320/w,d*1758/h))
                self.display_pil.paste(patch,(a,b));changed=True
            if changed:self.image.paste(self.display_pil)
        self.previous_source=im;self.previous_moving=self.moving;self.previous_theme=theme
        if getattr(self,'present_offset',None)!=self.offset:
            self.canvas.coords(self.canvas_item,*self.offset);self.present_offset=self.offset

    def draw_save(self,im,key,r,action,theme):
        self.control(key,r,action);h=self.tween('hover:'+key,float(self.hover_key==key),.14);p=self.tween('press:'+key,float(self.pressed_key==key or self.pulses.get(key,0)>self.last_now),.08)
        color=mix(mix((255,255,255),(38,33,47),theme),mix((230,230,236),(65,58,78),theme),h);color=mix(color,mix((205,205,215),(89,78,106),theme),p)
        self.fill(ImageDraw.Draw(im),(r[0]-2,r[1]-2,r[2]+2,r[3]+2),mix(self.card,(255,255,255),theme))
        shape_name='save_shape' if key=='save_prep' else 'save_advanced_shape'
        self.paste(im,self.colored(self.tile(shape_name),color),r[0],r[1]);label=self.tile('save_label')
        if theme:label=self.colored(label,mix((17,17,21),(255,255,255),theme))
        self.centered(im,label,r)
    def draw_segment(self,im,key,r,action,theme):
        self.control(key,r,action);h=self.tween('hover:'+key,float(self.hover_key==key),.14);p=self.tween('press:'+key,float(self.pressed_key==key or self.pulses.get(key,0)>self.last_now),.08);shape=self.tile(key+'_shape')
        if not (h or p or theme):return
        if h or p or theme:
            rgb=np.array(shape);mask=rgb[:,:,3]>0;target=mix((59,59,68),(227,223,235),theme);target=mix(target,mix((78,78,90),(209,202,222),theme),h);target=mix(target,mix((47,47,56),(187,176,204),theme),p);rgb[mask,:3]=target;shape=Image.fromarray(rgb)
        self.paste(im,shape,r[0],r[1])
        for part in ('label','icon'):
            name=key+'_'+part;b=self.meta[name]['bbox'];tile=self.tile(name)
            if theme:tile=self.colored(tile,mix((255,255,255),(36,34,42),theme))
            self.paste(im,tile,b[0],b[1])
    def draw_switch(self,im,key,var,theme,disabled,enabled):
        meta=self.meta['switch_'+key];r=tuple(meta['rect']);region=meta['region']
        def change():
            var.set(not var.get())
            if key!='dry':self.app.advanced_feedback.set('尚未保存')
        self.control(key,(r[0]-5,r[1]-7,r[2]+5,r[3]+7),change,enabled);value=self.tween('switch:'+key,float(var.get() and not (key in ('sound_on','sound_off','sound_exit') and self.app.mute_var.get())),.18)
        if theme==0 and disabled==0 and value==float(meta['default']):im.paste(self.tile('switch_'+key).convert('RGB'),(region[0]-60,region[1]-81));return
        w,h=region[2]-region[0],region[3]-region[1];s=4;tile=Image.new('RGB',(w*s,h*s),mix(self.card,(255,255,255),theme));d=ImageDraw.Draw(tile)
        x0=(r[0]-region[0])*s;y0=(r[1]-region[1])*s;x1=(r[2]-region[0])*s-1;y1=(r[3]-region[1])*s-1
        track=mix(mix((67,67,76),(213,209,220),theme),mix((255,255,255),(38,33,47),theme),value*(1-disabled));knob=mix((255,255,255),mix(self.card,(244,243,247),theme),value*(1-disabled))
        d.rounded_rectangle((x0,y0,x1,y1),radius=(y1-y0)/2,fill=track);radius=(y1-y0)/2-2*s;cx=x0+2*s+radius+value*((x1-x0)-4*s-2*radius);cy=(y0+y1)/2
        d.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill=knob);im.paste(tile.resize((w,h),Image.Resampling.LANCZOS),(region[0]-60,region[1]-81))
    def draw_notices(self,im,card,muted):
        for key in list(self.notices):
            message,start=self.notices[key];age=self.last_now-start;alpha=self.notice_alpha(age)
            if age>=3.54:
                del self.notices[key];var=self.app.prep_feedback if key=='prep' else self.app.advanced_feedback;var.set('');continue
            if age<.09 or age>=3.09:self.moving=True
            else:self.wake_at=min(self.wake_at or start+3.09,start+3.09)
            default='音效与快捷键已保存' if key=='prep' else '进阶自定义设置已保存';name='saved_prep' if key=='prep' else 'saved_advanced';r=self.meta[name]['bbox']
            tile=self.tile(name) if message==default else self.text_tile(message,22,muted,maxwidth=810)
            if self.theme=='light':tile=self.colored(tile,muted)
            self.paste(im,tile,r[0],r[1],alpha)
            if message==default:
                mark='yellow_mark' if key=='prep' else 'yellow_mark_advanced';b=self.meta[mark]['bbox']
                self.paste(im,self.tile(mark),b[0],b[1],alpha)
    def draw_status(self,im,card,fg,muted):
        state,color=self.current_state(self.palette());d=ImageDraw.Draw(im)
        if state!='未启动':self.draw_status_border(im,color)
        if state!='未启动':self.fill(d,(140,1521,1047,1602),card);self.paste(im,self.text_tile('当前状态：'+state,60,color,maxwidth=900),145,1530)
        if self.app.key_name!='F10' or not self.app.hotkeys or state!='未启动':
            self.fill(d,(140,1607,1044,1648),card);caption=self.app.status.get() if state!='未启动' or not self.app.hotkeys else f'进入战局后，请在左上角人物血量HUD可见后按{self.app.key_name}';self.paste(im,self.text_tile(caption,24,color,maxwidth=900),145,1616)
        if not self.app.dry.get():self.fill(d,(332,1661,1045,1702),card);self.paste(im,self.mode_caption(muted),334,1667)

    def draw_status_border(self,im,color):
        key=(color,self.theme)
        if key not in self.border_cache:
            data=self.meta['status_border'];r=data['bbox']
            mask=np.asarray(Image.open(self.art/'status_border_mask.png').convert('L'),dtype='float32')/255
            base=np.asarray((self.dark if self.theme=='dark' else self.light).crop(self.local(r)),dtype='float32')
            delta=np.array(ImageColor.getrgb(color))-np.array(data['stroke_rgb'])
            rgb=np.clip(base+mask[:,:,None]*delta,0,255).astype('uint8')
            rgba=np.dstack((rgb,np.where(mask>0,255,0).astype('uint8')))
            self.border_cache[key]=Image.fromarray(rgba)
        r=self.meta['status_border']['bbox'];self.paste(im,self.border_cache[key],r[0],r[1])
    def current_state(self,p):
        status=self.app.bottom_status.get()
        if self.app.active:return ('已暂停' if '暂停' in status else '监控中','#6DAE8A')
        if self.app.arming:return ('正在连接','#BB9B5D')
        if status.startswith('已确认游戏进程退出'):return ('游戏已退出','#6DAE8A')
        if '退出确认中' in status or '按键已发送' in status:return ('退出确认中','#BB9B5D')
        if '退出未确认' in status or '无法确认' in status:return ('退出未确认',p['accent'])
        if '演练候选' in status:return ('演练已触发','#AB8AD1')
        if '撤离' in status:return ('撤离已识别','#6DAE8A')
        return ('未启动',p['accent'])
    def preview(self):
        if self.app.mute_var.get() or self.app.sounds.muted:self.app.prep_feedback.set('静音模式已开启');return
        self.app.play_sound('exit')
    def check_hotkey(self):
        try:self.app.prep_feedback.set(self.app.key_name+('：未发现系统热键占用' if self.app.hotkey_service.available(self.app.hotkey_service.combo) else '：系统热键已被占用'))
        except RuntimeError:self.app.prep_feedback.set('快捷键检测超时，请稍后重试')
    def change_theme(self,v):self.theme=v;self.app.ui_theme=v;self.app.save_ui_theme();self.schedule()
    def coords(self,e):return ((e.x-self.offset[0])/self.scale+60,(e.y-self.offset[1])/self.scale+81)
    def target(self,e):
        if not self.controls:return None
        x,y=self.coords(e)
        for key,c in reversed(list(self.controls.items())):
            if c.enabled and c.action and c.rect[0]<=x<=c.rect[2] and c.rect[1]<=y<=c.rect[3]:return key
        return None
    def mouse_down(self,e):
        self.canvas.focus_set();self.focus_visible=False;key=self.target(e)
        if self.dropdown and not (key and (key.startswith(('drop_','delete_')) or key in ('audio_value','hotkey_value','audio_arrow','key_arrow','delay_value','hz_value','delay_arrow','hz_arrow'))):
            self.close_dropdown();self.pressed_key=None;return
        self.pressed_key=key;self.focus_key=key;self.schedule()
    def mouse_up(self,e):
        key=self.target(e);pressed=self.pressed_key;self.pressed_key=None
        if key is not None and key==pressed:self.pulses[key]=self.clock()+.06;self.controls[key].action()
        self.schedule()
    def click(self,e):self.mouse_down(e);self.mouse_up(e)
    def motion(self,e):
        key=self.target(e);self.canvas.configure(cursor='hand2' if key else '')
        if key!=self.hover_key:self.hover_key=key;self.schedule()
    def leave(self,e):self.hover_key=None;self.schedule()
    def focus_out(self,e):self.focus_visible=False;self.schedule()
    def tab(self,e,direction=1):
        keys=[k for k,c in self.controls.items() if c.enabled and c.action]
        if keys:
            i=keys.index(self.focus_key) if self.focus_key in keys else (-1 if direction==1 else 0);self.focus_key=keys[(i+direction)%len(keys)];self.focus_visible=True;self.schedule()
        return 'break'
    def activate(self,e):
        c=self.controls.get(self.focus_key)
        if c and c.enabled and c.action:self.pulses[self.focus_key]=self.clock()+.06;c.action();self.schedule()
        return 'break'
