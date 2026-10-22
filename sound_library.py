"""Persistent audio choices; decode only at import, never on the capture path."""
from pathlib import Path
import hashlib
import json
import os
import wave

PRESETS={'原版死亡回归':'return_by_death.wav','轻提示 · 开启':'enabled.wav',
         '轻提示 · 关闭':'disabled.wav','电子回溯':'rewind.wav','杀手皇后第三炸弹败者食尘':'killer_queen.wav','塔科夫任务完成电音':'quest_completed.wav','鸭科夫仓库区撤离':'duckov_extract.wav'}
DEFAULTS={'enabled':'轻提示 · 开启','disabled':'轻提示 · 关闭','exit':'原版死亡回归'}

class SoundLibrary:
    def __init__(self,base,assets):
        self.base=Path(base);self.assets=Path(assets)
        self.folder=self.base/'user_audio';self.config=self.base/'sound_settings.json'
        self.volumes={};self._device=None;self.deleted=[];self.custom={};self.selected=dict(DEFAULTS);self.enabled={k:True for k in DEFAULTS};self.muted=False
        try:
            data=json.loads(self.config.read_text(encoding='utf-8'))
            self.muted=data.get('muted',False) is True
            self.load_volumes(data.get('volumes',{}))
            for key in DEFAULTS:self.enabled[key]=data.get('enabled',{}).get(key,True) is not False
            for label,name in data.get('custom',{}).items():
                if isinstance(label,str) and isinstance(name,str) and Path(name).name==name and name.endswith('.wav'):
                    self.custom[label]=name
            for key in DEFAULTS:
                choice=data.get('selected',{}).get(key)
                if choice in self.choices() or (key=='exit' and choice is None):self.selected[key]=choice
        except (OSError,ValueError,TypeError,AttributeError):pass

    def choices(self):return list(PRESETS)+list(self.custom)

    def exit_choices(self):
        return [x for x in ([DEFAULTS["exit"],"杀手皇后第三炸弹败者食尘","塔科夫任务完成电音","鸭科夫仓库区撤离"]+list(self.custom)) if x not in self.deleted]

    def remove_exit(self,choice):
        if choice not in self.deleted:self.deleted.append(choice)
        if self.selected["exit"]==choice:self.selected["exit"]=next(iter(self.exit_choices()),None)

    def resolve(self,key):
        choice=self.selected[key]
        if choice is None:return None
        p=self.assets/PRESETS[choice] if choice in PRESETS else self.folder/self.custom.get(choice,'missing')
        return p if p.is_file() else self.assets/PRESETS[DEFAULTS[key]]

    def choose(self,key,choice,persist=False):
        if key not in DEFAULTS or choice not in self.choices():raise ValueError('无效音效选项')
        old=self.selected[key];self.selected[key]=choice
        try:
            if persist:self.save()
        except OSError:self.selected[key]=old;raise

    def save(self):
        self.base.mkdir(parents=True,exist_ok=True)
        temp=self.config.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(selected=self.selected,custom=self.custom,enabled=self.enabled,muted=self.muted,deleted=list(self.deleted),volumes=dict(self.volumes)),ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.config)

    def profile(self):
        return dict(selected=dict(self.selected),custom=dict(self.custom),enabled=dict(self.enabled),muted=self.muted,deleted=list(self.deleted),volumes=dict(self.volumes))

    def load_profile(self,data):
        if not isinstance(data,dict):return
        self.load_volumes(data.get('volumes',{}))
        self.deleted=[x for x in data.get('deleted',[]) if isinstance(x,str)]
        custom=data.get('custom',{})
        if isinstance(custom,dict):
            for label,name in custom.items():
                if isinstance(label,str) and isinstance(name,str) and Path(name).name==name and name.endswith('.wav'):self.custom[label]=name
        for key in DEFAULTS:
            choice=data.get('selected',{}).get(key)
            if choice in self.choices() or (key=='exit' and choice is None):self.selected[key]=choice
            self.enabled[key]=data.get('enabled',{}).get(key,True) is not False
        self.muted=data.get('muted',False) is True

    def prepare_import(self,source):
        import miniaudio
        p=Path(source)
        if p.suffix.lower() not in ('.wav','.mp3','.flac','.ogg'):raise ValueError('支持 WAV / MP3 / FLAC / OGG 音频')
        if not p.is_file() or not 0<p.stat().st_size<=50*1024*1024:raise ValueError('请选择不超过 50MB 的非空音频文件')
        self.folder.mkdir(parents=True,exist_ok=True)
        temp=self.folder/'importing.tmp'
        stream=None;frames=0;digest=hashlib.sha256()
        try:
            stream=miniaudio.stream_memory(p.read_bytes(),nchannels=1,sample_rate=44100,frames_to_read=4096)
            with wave.open(str(temp),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(44100)
                for chunk in stream:
                    frames+=len(chunk)
                    if frames>60*44100:raise ValueError('音频超过 60 秒，请先截取需要的片段')
                    raw=chunk.tobytes();digest.update(raw);out.writeframesraw(raw)
            if frames==0:raise ValueError('音频没有可播放内容')
            name='custom_'+digest.hexdigest()[:20]+'.wav'
            os.replace(temp,self.folder/name)
            return ('自定义 · '+p.stem[:24]+' · '+digest.hexdigest()[:6],name)
        except (ValueError,OSError):raise
        except Exception as exc:raise ValueError('无法解码音频，请确认文件未损坏且格式受支持') from exc
        finally:
            if stream is not None:stream.close()
            temp.unlink(missing_ok=True)

    def register(self,key,label,name):
        old=dict(self.custom);self.custom[label]=name
        if label in self.deleted:self.deleted.remove(label)
        try:self.choose(key,label)
        except OSError:self.custom=old;raise

    def load_volumes(self,values):
        self.volumes={k:max(0,min(100,int(v))) for k,v in values.items()
                      if isinstance(k,str) and isinstance(v,(int,float)) and 0<=v<=100} if isinstance(values,dict) else {}

    def volume(self,choice=None):
        return self.volumes.get(choice if choice is not None else self.selected['exit'],50)

    def set_volume(self,choice,value):
        if choice in self.choices():self.volumes[choice]=max(0,min(100,round(value)))

    def stop(self):
        device=self._device;self._device=None
        if device is not None:device.close()

    def play(self,key):
        """A private playback device; gain changes affect this clip, never system audio."""
        import miniaudio
        import numpy as np
        path=self.resolve(key)
        if path is None or self.muted:return False
        choice=self.selected[key]
        self.stop()
        source=miniaudio.stream_file(str(path),nchannels=1,sample_rate=44100,frames_to_read=1024)
        def samples():
            try:
                needed=yield b''
                while True:
                    raw=source.send(needed)
                    pcm=np.frombuffer(raw,dtype=np.int16)
                    needed=yield (pcm.astype(np.float32)*(self.volume(choice)/100.)).astype(np.int16).tobytes()
            except StopIteration:return
            finally:source.close()
        generator=samples();next(generator)
        device=None
        try:
            device=miniaudio.PlaybackDevice(nchannels=1,sample_rate=44100,buffersize_msec=50)
            device.start(generator);self._device=device
        except Exception as exc:
            generator.close()
            if device is not None:device.close()
            raise RuntimeError('无法播放音效') from exc
        return True
