"""Inline animated primary-panel dropdowns; no settings windows."""
from PIL import Image,ImageDraw
from tkinter import filedialog
from hotkey_service import validate,label

class Dropdowns:
    def setup_dropdowns(self):
        self.key_choices=[]
        for c in self.app._loaded_preferences.get('hotkey_choices',[(0,0x79),(0,0x74),(0,0x70)]):
            try:
                c=validate(c)
                if c not in self.key_choices:self.key_choices.append(c)
            except (TypeError,ValueError):pass
        if self.app.hotkeys and self.app.hotkey_service.combo not in self.key_choices:self.key_choices.append(self.app.hotkey_service.combo)
        self.dropdown=None;self.dropdown_paint=None;self.dropdown_free={};self.key_recording=False
        self.bin_image=Image.open(self.assets/'bin.png').convert('RGBA')
        self.canvas.bind('<KeyPress>',self.record_key)
        self.canvas.bind('<Escape>',lambda e:self.close_dropdown())
        self.canvas.bind('<MouseWheel>',self.dropdown_wheel)
        self.dropdown_scroll=0;self.values['dropdown']=0.
    def toggle_dropdown(self,kind):
        if self.dropdown==kind:self.close_dropdown();return
        self.values['dropdown']=0.;self.tweens.pop('dropdown',None)
        self.dropdown=kind;self.dropdown_paint=kind;self.dropdown_scroll=0;self.key_recording=False
        if kind=='key':
            self.dropdown_free={}
            for combo in self.key_choices:
                try:self.dropdown_free[combo]=self.app.hotkey_service.available(combo)
                except RuntimeError:self.dropdown_free[combo]=False
        self.schedule()
    def close_dropdown(self):
        self.closing_rows=getattr(self,'dropdown_rows',[])
        self.dropdown=None;self.key_recording=False;self.schedule();return 'break'
    def dropdown_wheel(self,e):
        if self.dropdown in ('audio','key'):
            count=len(self.app.sounds.exit_choices())-bool(self.app.sounds.selected['exit']) if self.dropdown=='audio' else len(self.key_choices)-bool(self.app.hotkeys)
            self.dropdown_scroll=max(0,min(max(0,count-4),self.dropdown_scroll+(-1 if e.delta>0 else 1)));self.schedule();return 'break'
    def choose_exit(self,choice):
        self.app.sounds.choose('exit',choice);self.app.prep_feedback.set('音效已即时生效 · 保存后下次保留');self.close_dropdown()
    def delete_exit(self,choice):
        self.app.sounds.remove_exit(choice);self.dropdown_scroll=min(self.dropdown_scroll,max(0,len(self.app.sounds.exit_choices())-4))
        self.app.prep_feedback.set('已从音效列表移除 · 保存后下次保留');self.schedule()
    def import_exit(self):
        self.close_dropdown()
        path=filedialog.askopenfilename(parent=self.app.root,title='选择大退音效',filetypes=[('音频文件','*.wav *.mp3 *.flac *.ogg'),('所有文件','*.*')])
        if path:self.app.import_sound('exit',path)
    def select_key(self,combo):
        try:
            combo=validate(combo)
            if not self.app.hotkey_service.rebind(combo):self.app.prep_feedback.set('该快捷键被占用，未修改');return
            self.app.cancel('快捷键已切换，请重新开启')
            if combo not in self.key_choices:self.key_choices.append(combo)
            self.app.key_name=self.app.hotkey_service.key_name;self.app.hotkeys=self.app.hotkey_service.registered
            self.app.key_label.set(self.app.key_name);self.app.key_hint.set('回到游戏后按 '+self.app.key_name+' 开启，再按一次停止')
            self.app.notice.data.szTip='ID486模拟助手 · '+self.app.key_name+' 开启/关闭'
            self.app.event('hotkey_changed',key=self.app.key_name)
            self.app.prep_feedback.set('快捷键已即时生效 · 保存后下次保留');self.close_dropdown()
        except (ValueError,RuntimeError) as exc:self.app.prep_feedback.set(str(exc))
    def delete_key(self,combo):
        if self.app.hotkeys and combo==self.app.hotkey_service.combo:
            self.app.cancel('快捷键已删除，请重新设置')
            self.app.hotkey_service.clear();self.app.hotkeys=self.app.hotkey_service.registered
            self.app.key_name='未设置';self.app.key_label.set('未设置')
        if combo in self.key_choices:self.key_choices.remove(combo)
        self.dropdown_scroll=min(self.dropdown_scroll,max(0,len(self.key_choices)-4))
        self.app.prep_feedback.set('已从快捷键列表移除 · 保存后下次保留');self.schedule()

    def start_key_recording(self):
        self.key_recording=True;self.canvas.focus_set();self.schedule()
    def record_key(self,e):
        if not self.key_recording:return
        if e.keysym=='Escape':return self.close_dropdown()
        if e.keysym in ('Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R'):return 'break'
        mods=(2 if e.state&4 else 0)|(1 if e.state&0x20000 else 0)|(4 if e.state&1 else 0)
        self.select_key((mods,e.keycode));return 'break'
    def dropdown_text(self,text,color,en,width):
        tile=self.text_tile(text,24,color,en=en)
        if tile.width<=width:return tile
        while text and self.text_tile(text+'…',24,color,en=en).width>width:text=text[:-1]
        return self.text_tile(text+'…',24,color,en=en)

    def select_number(self,kind,value):
        var=self.app.delay_text if kind=='delay' else self.app.hz_text
        var.set(str(value));self.app.advanced_feedback.set('尚未保存');self.close_dropdown()

    def draw_dropdown(self,im,theme):
        kind=self.dropdown or self.dropdown_paint
        if not kind:return
        # The overlay owns pointer/focus targets within its visible rectangle.
        audio=kind=='audio';numeric=kind in ('delay','hz')
        x0,y0,x1={'audio':(250,570,665),'key':(940,570,1253),'delay':(825,1135,920),'hz':(1178,1135,1255)}[kind];rowh=54
        if numeric:
            values=(150,300,500) if kind=='delay' else (15,30,60)
            current=self.app.delay_text.get() if kind=='delay' else self.app.hz_text.get()
            rows=[(str(v),lambda v=v:self.select_number(kind,v),True,None) for v in values if str(v)!=current]
        elif audio:
            choices=self.app.sounds.exit_choices();remaining=[x for x in choices if x!=self.app.sounds.selected['exit']]
            visible=remaining[self.dropdown_scroll:self.dropdown_scroll+4]
            rows=[(x,lambda x=x:self.choose_exit(x),True,x) for x in visible]+[('自定义+',self.import_exit,True,None)]
        else:
            remaining=[c for c in self.key_choices if not self.app.hotkeys or c!=self.app.hotkey_service.combo]
            rows=[(label(c),lambda c=c:self.select_key(c),self.dropdown_free.get(c,False),c) for c in remaining[self.dropdown_scroll:self.dropdown_scroll+4]]
            rows.append(('等待输入…（Esc取消）' if self.key_recording else '自定义+',self.start_key_recording,True,None))
        if not self.dropdown:rows=getattr(self,'closing_rows',rows) or rows
        self.dropdown_rows=rows
        # Fixed travel speed, rather than 200ms regardless of list height.
        duration=max(.06,len(rows)*rowh/900)
        progress=self.tween('dropdown',float(self.dropdown is not None),duration)
        if progress<=0:
            if not self.dropdown:self.dropdown_paint=None
            return
        height=round(len(rows)*rowh*progress)
        if self.dropdown:self.control('drop_block',(x0,y0,x1,y0+height),lambda:None)
        layer=Image.new('RGB',(x1-x0,len(rows)*rowh),(32,32,36) if theme<.5 else (247,245,249));d=ImageDraw.Draw(layer)
        fg=self.palette()['muted'];border=(119,119,125);bg=(32,32,36) if theme<.5 else (247,245,249)
        for i,(title,action,enabled,deletable) in enumerate(rows):
            key='drop_'+kind+'_'+str(i);y=i*rowh
            h=self.tween('hover:'+key,float(self.hover_key in (key,'delete_'+str(i))),.12)
            color=tuple(round(v+(18 if theme<.5 else -15)*h) for v in bg)
            d.rectangle((1,y,x1-x0-2,y+rowh),fill=color);d.line((0,y,x1-x0,y),fill=border)
            color_text=fg if enabled else '#666666'
            if not audio and title=='F10':
                a=self.text_tile('F10 ',24,color_text,en=True);b=self.text_tile('（默认）',24,color_text)
                tile=Image.new('RGBA',(a.width+b.width,max(a.height,b.height)))
                tile.alpha_composite(a,(0,tile.height-a.height));tile.alpha_composite(b,(a.width,tile.height-b.height))
            else:tile=self.dropdown_text(title,color_text,numeric or (not audio and deletable is not None),x1-x0-(70 if deletable else 35))
            layer.paste(tile,(24,y+(rowh-tile.height)//2),tile)
            if not enabled:d.line((24,y+rowh//2,min(x1-x0-20,24+tile.width),y+rowh//2),fill='#777777',width=2)
            r=(x0,y0+y,x1-(45 if deletable else 0),min(y0+y+rowh,y0+height))
            if self.dropdown and y+rowh<=height:self.control(key,r,action,enabled)
            if deletable:
                icon=self.colored(self.bin_image,'#AD3535');icon.thumbnail((24,24));layer.paste(icon,(x1-x0-40,y+(rowh-icon.height)//2),icon)
                if self.dropdown and y+rowh<=height:self.control('delete_'+str(i),(x1-45,y0+y,x1,y0+y+rowh),lambda x=deletable:(self.delete_exit(x) if audio else self.delete_key(x)))
        # Square top corners join the field; only the bottom corners round off.
        width=x1-x0;total=len(rows)*rowh;radius=10
        d.line((0,0,0,total-radius),fill=border);d.line((width-1,0,width-1,total-radius),fill=border)
        d.line((radius,total-1,width-radius-1,total-1),fill=border)
        d.arc((0,total-2*radius,2*radius,total-1),90,180,fill=border)
        d.arc((width-2*radius-1,total-2*radius,width-1,total-1),0,90,fill=border)
        mask=Image.new('L',layer.size,0);md=ImageDraw.Draw(mask)
        md.rounded_rectangle((0,0,width-1,total-1),radius=radius,fill=255)
        md.rectangle((0,0,width-1,total-radius-1),fill=255)
        layer=layer.convert('RGBA');layer.putalpha(mask)
        self.paste(im,layer.crop((0,0,x1-x0,height)),x0,y0)

