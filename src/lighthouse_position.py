"""Session-bound Lighthouse coordinate polling. No memory/network/process-exit APIs.

Protocol reference: NepPure/tarkov-offline-map (MIT), src/parsers.js and auto-shot.js.
Only an injected, foreground-checked callback can generate a game screenshot.
"""
from collections import deque
from pathlib import Path
from datetime import datetime
import json,math,os,re,threading,time

NUMBER=r'[+-]?(?:\d+(?:\.\d+)?|\.\d+)'
SHOT=re.compile(r'^\d{4}-\d{2}-\d{2}\[\d{2}-\d{2}\]_('+NUMBER+r'),\s*('+NUMBER+r'),\s*('+NUMBER+r')_('+NUMBER+r'),\s*('+NUMBER+r'),\s*('+NUMBER+r'),\s*('+NUMBER+r')(?:_'+NUMBER+r')?\s*\(\d+\)\.png$',re.I)
SESSION=re.compile(r'^log_(\d{4}\.\d{2}\.\d{2}_\d{1,2}-\d{2}-\d{2})_')
APPLICATION=re.compile(r'(?:^|[ _])application(?:_(\d+))?\.log$',re.I)


def parse_position(name):
    match=SHOT.fullmatch(Path(name).name)
    if not match:return None
    values=tuple(float(v) for v in match.groups())
    if not all(math.isfinite(v) for v in values) or max(abs(v) for v in values[:3])>100000:return None
    return values[:3]


def map_event(line):
    if '|application|' not in line:return None
    text=line.split('|application|',1)[1]
    if re.match(r'(?:Matching with group id:|GameStopped:|GameEnded:)',text):return ('reset',None)
    m=re.search(r'scene preset path:\s*maps/([A-Za-z0-9_]+)\.bundle',text,re.I)
    if m:
        name=re.sub(r'_preset$','',m[1],flags=re.I).lower()
        return ('map','Lighthouse' if name=='lighthouse' else name)
    m=re.search(r'TRACE-NetworkGameCreate.*?Location:\s*([A-Za-z0-9_]+)',text,re.I)
    if m:return ('map','Lighthouse' if m[1].lower()=='lighthouse' else m[1].lower())
    # Transit summaries describe the route/history, including Count:0 during
    # raid startup. They don't establish that the player is leaving this map.
    # Reset on the explicit lifecycle markers or a new map observation above.
    return None


def bind_session(logs,started,wall):
    candidates=[]
    for folder in Path(logs).iterdir():
        m=SESSION.match(folder.name)
        if not m or not folder.is_dir():continue
        try:stamp=datetime.strptime(m[1],'%Y.%m.%d_%H-%M-%S').timestamp()
        except ValueError:continue
        if -5<=stamp-started<=180 and stamp<=wall+1:candidates.append((abs(stamp-started),folder))
    if not candidates:raise OSError('当前游戏会话日志尚未找到')
    return min(candidates,key=lambda row:row[0])[1]


class MapLog:
    """Backfill current process session once, then read bounded complete new lines."""
    def __init__(self,folder):
        self.folder=Path(folder);self.offsets={};self.pending={}

    def poll(self):
        events=[]
        paths=[]
        for p in self.folder.glob('*.log'):
            m=APPLICATION.search(p.name)
            if m:paths.append((int(m[1] or 0),p))
        for _,p in sorted(paths)[-2:]:
            size=p.stat().st_size
            offset=self.offsets.get(p)
            bootstrap=offset is None
            if bootstrap:offset=max(0,size-8*1024*1024)
            if size<offset:offset=0;self.pending.pop(p,None);events.append(('uncertain',None))
            with p.open('rb') as stream:
                stream.seek(offset);raw=stream.read(512*1024 if not bootstrap else 8*1024*1024)
                self.offsets[p]=stream.tell()
            raw=self.pending.pop(p,b'')+raw
            if bootstrap and offset:raw=raw.split(b'\n',1)[-1]
            lines=raw.split(b'\n');self.pending[p]=lines.pop()[-16384:]
            for line in lines:
                event=map_event(line[:16384].decode('utf-8',errors='replace'))
                if event:events.append(event)
        return events


# Next screenshot = time a sprinting player needs to reach the nearest
# protection boundary, minus capture latency, clamped. Far away = rare
# screenshots; at the boundary = frequent; standing by the trader = the time
# it takes to run out of the exit radius.
CADENCE=dict(max_speed=6.,latency=1.5,min_interval=3.,max_interval=60.,initial_interval=30.)
CADENCE_LIMITS=dict(max_speed=(1.,20.),latency=(0.,10.),min_interval=(2.,30.),max_interval=(5.,300.),initial_interval=(2.,300.))


class PositionPolicy:
    def __init__(self,zone):
        self.zone=dict(zone);self.map=None;self.epoch=0;self.protected=False
        self.position=None;self.observed=None;self.distance=None
        self.uncertain=False
        center=self.zone['center']
        if len(center)!=3 or not all(math.isfinite(float(v)) for v in center):raise ValueError('区域中心无效')
        if not 0<float(zone['enter_radius'])<float(zone['exit_radius']):raise ValueError('区域边界无效')
        if not float(zone['height_min'])<float(zone['height_max']):raise ValueError('区域高度无效')
        cadence=dict(CADENCE)
        if isinstance(zone.get('cadence'),dict):cadence.update(zone['cadence'])
        try:cadence={k:float(cadence[k]) for k in CADENCE}
        except (TypeError,ValueError):raise ValueError('截图频率参数无效')
        if (any(not math.isfinite(v) or not CADENCE_LIMITS[k][0]<=v<=CADENCE_LIMITS[k][1] for k,v in cadence.items())
                or cadence['min_interval']>cadence['max_interval']):raise ValueError('截图频率参数超出范围')
        self.cadence=cadence;self.interval=cadence['initial_interval']

    def next_interval(self,distance):
        """Call after self.protected reflects the new observation."""
        z=self.zone;c=self.cadence
        gap=float(z['exit_radius'])-distance if self.protected else distance-float(z['enter_radius'])
        return min(c['max_interval'],max(c['min_interval'],max(0.,gap)/c['max_speed']-c['latency']))

    def reset_map(self,name):
        retain=self.uncertain and self.protected and name=='Lighthouse'
        self.epoch+=1;self.map=name;self.position=None;self.observed=None
        self.distance=None;self.protected=retain;self.interval=self.cadence['initial_interval'];self.uncertain=False

    def invalidate_map(self):
        self.epoch+=1;self.map=None;self.position=None;self.observed=None
        self.distance=None;self.uncertain=True

    def observe(self,position,now):
        if self.map!='Lighthouse' or self.observed is not None and now<self.observed:return False
        if len(position)!=3 or not all(math.isfinite(v) for v in position):return False
        x,y,z=position;cx,_,cz=self.zone['center']
        distance=math.hypot(x-cx,z-cz)
        radius=self.zone['exit_radius'] if self.protected else self.zone['enter_radius']
        self.protected=bool(distance<=radius and self.zone['height_min']<=y<=self.zone['height_max'])
        self.position=tuple(position);self.distance=distance;self.observed=now
        self.interval=round(self.next_interval(distance),1)
        return True

    def snapshot(self,now):
        stale=self.uncertain or self.observed is not None and now-self.observed>max(12.,self.interval*2+8)
        return dict(map=self.map,epoch=self.epoch,protected=self.protected,position=self.position,
            distance=None if self.distance is None else round(self.distance,2),interval=self.interval,
            stale=stale,age=None if self.observed is None else round(now-self.observed,2))


class ScreenshotRetention:
    """Keep only the newest `limit` screenshots this tool requested.

    Ownership is recorded in a manifest beside the settings, so the cap holds
    across sessions. Files the player took by hand are never listed or deleted.
    """
    def __init__(self,manifest,roots,limit=5,min_age=10.,wall=None):
        self.manifest=Path(manifest);self.roots=tuple(Path(r).resolve() for r in roots)
        self.limit=max(0,int(limit));self.min_age=float(min_age);self.wall=wall or time.time
        self.paths=[];self.deleted=0
        try:
            data=json.loads(self.manifest.read_text(encoding='utf-8'))
            if isinstance(data,dict) and isinstance(data.get('owned'),list):self.paths=[str(p) for p in data['owned'] if isinstance(p,str)]
        except (OSError,ValueError):pass

    def owned(self,path):
        try:path=Path(path).resolve()
        except OSError:return False
        return bool(SHOT.fullmatch(path.name)) and any(path.parent==root for root in self.roots)

    def claim(self,path):
        path=str(Path(path))
        if not self.owned(path) or path in self.paths:return False
        self.paths.append(path);self._save();return True

    def prune(self):
        before=list(self.paths);keep=[]
        self.paths=[p for p in self.paths if self.owned(p) and Path(p).is_file()]
        excess=len(self.paths)-self.limit;now=self.wall()
        for p in self.paths:
            if excess>0:
                try:
                    if now-Path(p).stat().st_mtime>=self.min_age:
                        Path(p).unlink();excess-=1;self.deleted+=1;continue
                except FileNotFoundError:excess-=1;continue
                except OSError:pass
            keep.append(p)
        self.paths=keep
        if self.paths!=before:self._save()

    def _save(self):
        try:
            self.manifest.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.manifest.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(owned=self.paths),ensure_ascii=False),encoding='utf-8')
            os.replace(temporary,self.manifest)
        except OSError:pass


def default_screenshot_roots():
    import ctypes
    buffer=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None,5,None,0,buffer):raise OSError('无法定位文档目录')
    root=Path(buffer.value)/'Escape from Tarkov'
    return (root,root/'Screenshots')


class LocationMonitor:
    def __init__(self,folder,zone,roots,press,alive,foreground,event,status,action_lock=None,clock=None,wall=None,retention=None):
        self.log=MapLog(folder);self.policy=PositionPolicy(zone);self.roots=tuple(Path(p) for p in roots)
        self.press=press;self.alive=alive;self.foreground=foreground;self.event=event;self.status=status
        self.lock=action_lock or threading.RLock();self.clock=clock or time.perf_counter;self.wall=wall or time.time
        self.closed=threading.Event();self.thread=None;self.next_shot=0.;self.pending=None;self.failures=0
        self.paused=False
        self.hud_epoch=None;self.hud_after=self.clock()
        self.next_scan=0.;self.next_logs=0.;self.files={};self.last_status=None;self.accept_after=self.wall()
        self.shots=0;self.positions=0;self.retention=retention;self.pressed=deque(maxlen=8);self._baseline()
        if self.retention:self.retention.prune()

    def _entries(self):
        for root in self.roots:
            if not root.is_dir():continue
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.name.lower().endswith('.png') and entry.is_file():
                        stat=entry.stat();yield Path(entry.path),(stat.st_mtime_ns,stat.st_size)

    def _baseline(self):
        self.files={p:stamp for p,stamp in self._entries()}

    def snapshot(self):
        with self.lock:return dict(self.policy.snapshot(self.clock()),failures=self.failures)

    def confirm_hud(self,observed_at):
        with self.lock:
            if self.paused or self.policy.map!='Lighthouse' or observed_at<self.hud_after:return
            if self.hud_epoch!=self.policy.epoch:
                self.hud_epoch=self.policy.epoch
                self.event('lighthouse_hud_confirmed',epoch=self.hud_epoch)

    def set_paused(self,paused):
        with self.lock:
            if self.paused==bool(paused):return
            self.paused=bool(paused)
            self.pending=None;self.failures=0
            self.hud_epoch=None;self.hud_after=self.clock()
            self.policy.reset_map(self.policy.map)
            self.accept_after=self.wall();self.pressed.clear()
            self._baseline();self.next_shot=self.clock()
            self.event('lighthouse_polling_paused' if self.paused else 'lighthouse_polling_resumed',
                       map=self.policy.map,reason='low_frequency_wait' if self.paused else 'combat_hud_restored')

    def _publish(self):
        snapshot=self.policy.snapshot(self.clock());snapshot['failures']=self.failures
        # interval changes with every coordinate; it is reported per shot instead.
        signature=(snapshot['map'],snapshot['protected'],snapshot['stale'],self.failures>=3)
        if signature!=self.last_status:
            self.last_status=signature;self.event('lighthouse_position_state',**snapshot);self.status(snapshot)

    def tick(self):
        if self.closed.is_set() or not self.alive():return
        now=self.clock()
        if now>=self.next_logs:
            events=self.log.poll();self.next_logs=now+1.
            with self.lock:
                for kind,name in events:
                    # Duplicate scene/create lines within one initial backfill
                    # still clear old coordinates, never inherit another raid.
                    if kind=='uncertain':self.policy.invalidate_map()
                    else:self.policy.reset_map(name if kind=='map' else None)
                    self.hud_epoch=None;self.hud_after=self.clock()
                    self.accept_after=self.wall();self.pending=None;self.failures=0;self.next_shot=now
                    self._baseline()
                    self.event('lighthouse_map',map=self.policy.map,epoch=self.policy.epoch,source='current_process_application_log')
        with self.lock:
            if self.paused:return
        if now>=self.next_scan:
            entries=list(self._entries());self.next_scan=now+1.
            with self.lock:
                if self.closed.is_set() or not self.alive() or self.paused:return
                for path,stamp in sorted(entries,key=lambda row:row[1][0]):
                    previous=self.files.get(path);self.files[path]=stamp
                    if previous==stamp or not self.accept_after-.05<=stamp[0]/1e9<=self.wall()+5. or not stamp[1]:continue
                    # Only a file written right after our own key press is ours to delete later.
                    if self.retention and any(-.5<=stamp[0]/1e9-pressed<=8. for pressed in self.pressed):self.retention.claim(path)
                    position=parse_position(path.name)
                    if not position or self.policy.map!='Lighthouse':continue
                    try:captured=datetime.strptime(path.name[:17],'%Y-%m-%d[%H-%M]').timestamp()
                    except ValueError:continue
                    if not -65<=self.wall()-captured<=120:continue
                    if self.policy.observe(position,self.clock()):
                        self.pending=None;self.failures=0;self.positions+=1
                        self.next_shot=self.clock()+self.policy.interval
                        self.event('lighthouse_coordinate',**self.policy.snapshot(self.clock()),file=path.name)
                if self.retention:self.retention.prune()
        with self.lock:
            if self.paused:return
            if self.pending and now-self.pending[0]>=8.:
                self.failures+=1;self.pending=None;self.next_shot=now+3.
                self.event('lighthouse_screenshot_missing',failures=self.failures,paused=self.failures>=3)
            if (self.policy.map=='Lighthouse' and self.hud_epoch==self.policy.epoch
                    and now>=self.next_shot and self.pending is None and self.failures<3):
                if self.alive() and self.foreground() and not self.closed.is_set():
                    result=self.press()
                    if result is True:
                        self.pending=(self.clock(),self.policy.epoch);self.shots+=1;self.pressed.append(self.wall())
                        self.next_shot=self.clock()+self.policy.interval
                        self.event('lighthouse_screenshot_requested',interval=self.policy.interval,epoch=self.policy.epoch)
                    elif result is False:
                        self.failures+=1;self.next_shot=self.clock()+self.policy.interval
                        self.event('lighthouse_position_error',reason='screenshot_input_rejected',failures=self.failures)
            self._publish()

    def start(self):
        self.thread=threading.Thread(target=self._run,name='LighthouseCoordinates',daemon=True);self.thread.start()

    def _run(self):
        try:
            while not self.closed.is_set() and self.alive():
                try:self.tick()
                except (OSError,ValueError) as exc:
                    self.event('lighthouse_position_error',error_type=type(exc).__name__,reason=str(exc))
                    with self.lock:self.failures=3;self._publish()
                self.closed.wait(.2)
        finally:self.closed.set()

    def close(self,wait=True):
        self.closed.set()
        if wait and self.thread and threading.current_thread() is not self.thread:self.thread.join(timeout=2.)
