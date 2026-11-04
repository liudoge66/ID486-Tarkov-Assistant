"""Bounded append-only observation of one verified game's backend log.

No game memory, network requests, capture, input or exit APIs. A hint is never
an instruction to exit: the visual decision engine must independently confirm.
"""
from pathlib import Path
from datetime import datetime
import re, threading, time

SESSION = re.compile(r'^log_(\d{4}\.\d{2}\.\d{2}_\d{1,2}-\d{2}-\d{2})_')
BACKEND = re.compile(r'(?:^|[ _])backend_(\d+)\.log$', re.I)
REQUEST = re.compile(r'--->\s*Request HTTPS,\s*id\s*\[(\d+)\]:\s*URL:\s*https://([A-Za-z0-9.-]+)/client/match/local/end(?=[?\s]|\.\s|$)')


class SettlementWatch:
    def __init__(self, logs, process_started, pid, event, wall=None, clock=None, on_request=None):
        self.logs=Path(logs);self.started=float(process_started);self.pid=pid
        self.on_request=on_request;self.seen_attempts=set()
        self.event=event;self.wall=wall or time.time;self.clock=clock or time.perf_counter
        self.armed_wall=self.wall();self.folder=None;self.offsets={};self.oversized=set()
        self.seen=set();self.latest=None;self.lock=threading.Lock()
        self.closed=threading.Event();self.wake=threading.Event();self.thread=None;self.last_observed=None
        self.last_scan=-100.;self.paths=[];self.errors=0
        self.read_bytes=0;self.matched_requests=0;self.valid_hints=0
        self._bind()

    def _bind(self):
        candidates=[]
        for path in self.logs.iterdir():
            match=SESSION.match(path.name)
            if not path.is_dir() or not match:continue
            try:stamp=datetime.strptime(match.group(1),'%Y.%m.%d_%H-%M-%S').timestamp()
            except ValueError:continue
            # Pin the nearest startup folder, never the most recently modified
            # old session or a directory belonging to a later game restart.
            if -5<=stamp-self.started<=180 and stamp<=self.armed_wall+1:
                candidates.append((abs(stamp-self.started),path))
        if not candidates:raise OSError('当前游戏会话日志尚未找到，继续使用画面判断')
        self.folder=min(candidates,key=lambda item:item[0])[1]
        self._scan(initial=True)
        self.event('settlement_watch_started',pid=self.pid,session=self.folder.name,
                   files=[p.name for p in self.paths],skip_existing=True,route='/client/match/local/end')

    def _scan(self, initial=False):
        paths=[]
        for path in self.folder.glob('*.log'):
            match=BACKEND.search(path.name)
            if match:paths.append((int(match.group(1)),path))
        self.paths=[p for _,p in sorted(paths)][-2:]
        for path in self.paths:
            if path not in self.offsets:self.offsets[path]=path.stat().st_size if initial else 0
        self.last_scan=self.clock()

    def _line(self, raw, path):
        # Examine only the short request header. Never retain URL query values,
        # tokens, request/response bodies or unrelated log messages.
        text=raw.decode('utf-8',errors='replace');parts=text.split('|',4)
        if len(parts)!=5 or parts[2] not in ('Info','Error','Warn') or parts[3]!='backend':return
        header=parts[4].split('responseText:',1)[0]
        match=REQUEST.match(header)
        if not match:
            endpoint=re.search(r'https://([A-Za-z0-9.-]+)/client/match/local/end(?=[?\s]|\.\s|$)',header)
            if not endpoint:return
            host=endpoint.group(1).lower()
            if not any(host==root or host.endswith('.'+root) for root in ('escapefromtarkov.com','escapefromtarkov.ru')):return
            try:logged=datetime.strptime(text[:23],'%Y-%m-%d %H:%M:%S.%f').timestamp()
            except ValueError:return
            if logged<self.armed_wall-.1 or not -.25<=self.wall()-logged<=2.:return
            if '<---' in header:
                status=re.search(r'responseCode:(\d+)',header)
                ident=re.search(r'id\s*\[(\d+)\]',header)
                self.event('settlement_end_response',pid=self.pid,session=self.folder.name,
                    source_timestamp=text[:23],request_id=ident.group(1) if ident else None,
                    response_code=int(status.group(1)) if status else None,
                    network_error=bool(re.search(r'isNetworkError:True|ConnectionError',header)))
            return
        host=match.group(2).lower()
        if not any(host==root or host.endswith('.'+root) for root in ('escapefromtarkov.com','escapefromtarkov.ru')):return
        request_id=match.group(1)
        self.matched_requests+=1
        retry_match=re.search(r'\bretry=(\d+)',header)
        retry=int(retry_match.group(1)) if retry_match else 0
        attempt=(request_id,retry)
        if attempt in self.seen_attempts:return
        try:logged=datetime.strptime(text[:23],'%Y-%m-%d %H:%M:%S.%f').timestamp()
        except ValueError:return
        wall=self.wall();mono=self.clock();lag=wall-logged
        hint=dict(request_id=request_id,pid=self.pid,session=self.folder.name,
                  file=path.name,source_log_path=str(path),request_host=host,request_route='/client/match/local/end',source_timestamp=text[:23],seen_wall_ns=int(wall*1e9),
                  seen_monotonic=mono,log_lag_ms=round(lag*1000,3))
        if logged<self.armed_wall-.1 or not -.25<=lag<=2.:
            self.event('settlement_log_stale',**hint);return
        self.seen_attempts.add(attempt)
        if len(self.seen_attempts)>512:self.seen_attempts={attempt}
        if request_id in self.seen:
            self.event('settlement_end_retry',**hint,retry=retry)
            if self.on_request:self.on_request(dict(hint,retry=retry))
            return # Retry audit must not overwrite the first request's visual hint.
        self.seen.add(request_id)
        if len(self.seen)>512:self.seen={request_id}
        with self.lock:self.latest=hint;self.last_observed=hint
        self.valid_hints+=1
        self.wake.set()
        if self.on_request:self.on_request(dict(hint,retry=retry))
        self.event('settlement_log_seen',**hint)

    def poll(self):
        if self.clock()-self.last_scan>=.25:self._scan()
        budget=262144
        for path in self.paths:
            if budget<=0:break
            size=path.stat().st_size
            if size<self.offsets[path]:
                # Truncation is not proof that old bytes are new events.
                self.offsets[path]=size;self.oversized.discard(path);continue
            if size==self.offsets[path]:continue
            with path.open('rb') as stream:
                stream.seek(self.offsets[path])
                while budget>0:
                    position=stream.tell();raw=stream.readline(min(4097,budget))
                    if not raw:break
                    self.read_bytes+=len(raw)
                    budget-=len(raw)
                    if not raw.endswith(b'\n'):
                        if len(raw)<4097 and path not in self.oversized:
                            stream.seek(position);break
                        self.oversized.add(path);self.offsets[path]=stream.tell();continue
                    self.offsets[path]=stream.tell()
                    if path in self.oversized:self.oversized.discard(path);continue
                    self._line(raw,path)

    def peek(self, now=None):
        now=self.clock() if now is None else now
        with self.lock:hint=self.latest
        return dict(hint) if hint and 0<=now-hint['seen_monotonic']<=1. else None

    def discard(self):
        with self.lock:self.latest=None
        self.wake.clear()

    def start(self):
        def run():
            while not self.closed.wait(.01):
                try:self.poll();self.errors=0
                except OSError as exc:
                    self.errors+=1
                    if self.errors==1:self.event('settlement_watch_error',pid=self.pid,error_type=type(exc).__name__)
                    if self.errors>=10:return # Visual monitoring remains independent.
        self.thread=threading.Thread(target=run,name='SettlementLogWatch',daemon=True);self.thread.start()

    def close(self):
        if self.closed.is_set():return
        self.closed.set();self.wake.set()
        if self.thread:self.thread.join(timeout=.3)
        unread=0
        for path in self.paths:
            try:
                with path.open('rb') as stream:stream.seek(0,2);unread+=max(0,stream.tell()-self.offsets[path])
            except OSError:pass
        with self.lock:last=dict(self.last_observed) if self.last_observed else {}
        self.event('game_end_request_summary',pid=self.pid,found=bool(self.valid_hints),
                   source_timestamp=last.get('source_timestamp'),request_id=last.get('request_id'),
                   source_log_path=last.get('source_log_path'),log_paths=[str(p) for p in self.paths],observation_only=True,
                   meaning='client_log_observation_not_server_receipt')
        self.event('settlement_watch_stopped',pid=self.pid,session=self.folder.name,
                   files=[p.name for p in self.paths],read_bytes=self.read_bytes,
                   matched_requests=self.matched_requests,valid_hints=self.valid_hints,unread_tail_bytes=unread)
