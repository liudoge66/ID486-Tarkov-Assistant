"""Fence network policy: verified current-session evidence, bounded leases, online on doubt.

No packet payload interception or game credentials. The elevated broker owns all
filters and independently expires a lease if this controller stops responding.
"""
from datetime import datetime, timezone
from pathlib import Path
import ctypes as C
from ctypes import wintypes as W
import ipaddress, json, math, os, queue, re, socket, struct, threading, time, uuid
from broker_ipc import read_snapshot

HOST='gw-pve.escapefromtarkov.com'
BACKEND_HOSTS=(HOST,'gw-pve.escapefromtarkov.ru')
VENDOR_ROOTS=('escapefromtarkov.com','escapefromtarkov.ru','battlestategames.com')
DOMAIN_KEYWORD='escapefromtarkov'
ROUTE='/client/game/keepalive'
DEFAULT_SECONDS=120
MAX_SECONDS=170
RECONNECT_TIMEOUT=10.

def backend_host(line):
    match=re.search(r'\bhttps?://([A-Za-z0-9.-]+)(?=[:/\s,]|$)',line)
    host=match.group(1).lower().rstrip('.') if match else None
    return host if host and vendor_host(host) else None

def vendor_host(host):
    host=host.lower().rstrip('.')
    return DOMAIN_KEYWORD in host or host=='battlestategames.com' or host.endswith('.battlestategames.com')

def select_backend_log(folder):
    # Queue logs share the word backend but never contain HTTPS keepalive pairs.
    candidates=[p for p in Path(folder).glob('*.log') if re.search(r'(?:^|[ _])backend_\d+\.log$',p.name,re.I)]
    return max(candidates,key=lambda p:p.stat().st_mtime) if candidates else None

def http_status(line):
    # Only labeled status fields / an HTTP status line. Durations such as
    # SumSeconds: 0.401 and URL query values are never HTTP error codes.
    metadata=re.sub(r'https?://[^\s,]+','',line)
    match=re.search(r'(?:\bHTTP(?:/\d(?:\.\d)?)?\s+|(?<!\w)(?:responseCode|statusCode|status_code|httpStatus|httpCode)[\"\']?\s*[:=]\s*[\"\']?)([1-5]\d{2})(?!\d|\.\d)',metadata,re.I)
    return int(match.group(1)) if match else None

def response_error(line):
    status=http_status(line)
    if status is not None and status>=400:return True
    err=re.search(r'"err"\s*:\s*(-?\d+)\b',line)
    if err and int(err.group(1))!=0:return True
    return bool(re.search(r'\b(?:isNetworkError|isHttpError)\s*[:=]\s*true\b|\b(?:HTTPNetworkException|HttpRequestException|SocketException|TaskCanceledException|TimeoutException)\b',line,re.I)
        or re.search(r'^\d{4}-\d{2}-\d{2} [^|]*\|[^|]*\|(?:Error|Exception)\|',line,re.I))

def session_error(line):
    return http_status(line) in (401,403) or bool(re.search(r'\b(?:BADSESSION|InvalidSession|SessionExpired|Unauthorized)\b|\bsession\b.{0,80}\b(?:expired|invalid)\b',line,re.I))

class CyclePolicy:
    def __init__(self, seconds=DEFAULT_SECONDS):
        if not 1<=seconds<=MAX_SECONDS:raise ValueError('每轮隔离必须为1–170秒，默认120秒')
        self.seconds=seconds;self.enabled=False;self.state='off';self.hold=False
        self.last_request=None;self.period=None;self.deadline=None;self.online_after=0
        self.first_started=False
    def enable(self, now):
        self.enabled=True;self.state='wait_online';self.online_after=now;self.hold=False;self.deadline=None;self.first_started=False
    def disable(self):self.enabled=False;self.state='off';self.deadline=None
    def restore(self, now, hold=False):
        self.state='wait_online' if self.enabled else 'off';self.online_after=now;self.deadline=None;self.hold=hold
    def immediate_first(self,now):
        if not self.enabled or self.hold or self.state!='wait_online' or self.first_started:return None
        self.first_started=True;duration=min(self.seconds,MAX_SECONDS)
        self.deadline=now+duration;self.state='starting';return duration
    def confirmed(self, request_at, response_at):
        previous=self.last_request;self.last_request=request_at
        if previous is not None and 30<=request_at-previous<=600:self.period=request_at-previous
        # A request blocked before restoration may complete AFTER restoration.
        if not self.enabled or self.hold or self.state!='wait_online' or response_at<=self.online_after:return None
        duration=min(self.seconds,MAX_SECONDS)
        if self.period is not None:duration=min(duration,self.period-2-(response_at-request_at))
        if duration<1:return None
        self.deadline=response_at+duration;self.state='starting';return duration
    def isolated(self):self.state='isolated'
    def due(self, now):return self.deadline is not None and now>=self.deadline

class BackendEvidence:
    """Pair only newly observed request IDs; an empty response is NOT business success."""
    def __init__(self):self.requests={};self.transport_seen=False
    def parse(self,line,now):
        host=backend_host(line)
        if host is None or ROUTE not in line:return None
        ident=re.search(r'id\s*\[(\d+)\]',line)
        if not ident:return None
        ident=(host,ident.group(1))
        if '--->' in line or ('Request HTTPS' in line and '<---' not in line):
            self.requests[ident]=now
            if len(self.requests)>20:self.requests.pop(next(iter(self.requests)))
            return None
        if '<---' not in line or 'Response' not in line:return None
        request=self.requests.pop(ident,None)
        if request is None:return None
        self.transport_seen=True
        # Explicit protocol success or structured backend success is required.
        status=http_status(line)
        success=status is not None and 200<=status<300
        success=success or bool(re.search(r'"err"\s*:\s*0\b',line))
        failed=response_error(line) or session_error(line)
        if failed:return dict(kind='failed',host=host,request=request,response=now)
        return dict(kind='confirmed' if success else 'transport_only',host=host,request=request,response=now)

def find_game():
    k=C.WinDLL('kernel32',use_last_error=True)
    class Entry(C.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',C.c_size_t),
                  ('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('exe',W.WCHAR*260)]
    k.CreateToolhelp32Snapshot.restype=W.HANDLE;k.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD]
    k.Process32FirstW.argtypes=[W.HANDLE,C.POINTER(Entry)];k.Process32NextW.argtypes=k.Process32FirstW.argtypes
    k.CloseHandle.argtypes=[W.HANDLE]
    snapshot=k.CreateToolhelp32Snapshot(2,0)
    if snapshot in (None,C.c_void_p(-1).value):return None
    entry=Entry();entry.size=C.sizeof(entry);found=[]
    try:
        more=k.Process32FirstW(snapshot,C.byref(entry))
        while more:
            if entry.exe.lower()=='escapefromtarkov.exe':found.append(int(entry.pid))
            more=k.Process32NextW(snapshot,C.byref(entry))
    finally:k.CloseHandle(snapshot)
    if len(found)!=1:return None
    return found[0]

def process_path(handle):
    k=C.WinDLL('kernel32');k.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
    name=C.create_unicode_buffer(32768);n=W.DWORD(len(name))
    if not k.QueryFullProcessImageNameW(handle,0,name,C.byref(n)):raise OSError('无法核实游戏路径')
    return Path(name.value)

def resolve_targets():
    addresses=set()
    for host in BACKEND_HOSTS:
        try:addresses.update(ipaddress.ip_address(item[4][0]).compressed for item in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM))
        except socket.gaierror:continue
    if not addresses or len(addresses)>64 or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError('PvE网关地址无法核实，保持联网')
    return sorted(addresses)

def connected_addresses(pid):
    """Read-only owner-PID TCP table; reject unverified proxy/accelerator routing."""
    api=C.WinDLL('iphlpapi').GetExtendedTcpTable
    api.argtypes=[C.c_void_p,C.POINTER(W.DWORD),W.BOOL,W.ULONG,C.c_int,W.ULONG]
    result=set()
    for family,row_size in ((2,24),(23,56)):
        size=W.DWORD();code=api(None,C.byref(size),False,family,5,0)
        if code not in (0,122):raise OSError('无法读取游戏连接表')
        buffer=C.create_string_buffer(size.value);code=api(buffer,C.byref(size),False,family,5,0)
        if code:raise OSError('无法读取游戏连接表')
        raw=buffer.raw;count=struct.unpack_from('<I',raw)[0]
        for i in range(count):
            offset=4+i*row_size
            if family==2:
                state,_,_,remote,_,owner=struct.unpack_from('<6I',raw,offset)
                address=socket.inet_ntop(socket.AF_INET,struct.pack('<I',remote))
            else:
                state,owner=struct.unpack_from('<2I',raw,offset+48)
                address=socket.inet_ntop(socket.AF_INET6,raw[offset+24:offset+40])
            if owner==pid and state==5:result.add(ipaddress.ip_address(address).compressed)
    return result

class NetworkController:
    def __init__(self, base, assets, event, status):
        self.base=Path(base);self.assets=Path(assets);self.event=event;self.status=status
        self.commands=queue.Queue();self.closed=threading.Event();self.work_wake=threading.Event();self.broker_wake=None;self.enabled=False;self.policy=CyclePolicy()
        self.pid=None;self.game=None;self.folder=None;self.handle=None;self.stream=None;self.scan_at=0
        self.evidence=BackendEvidence();self.backend=None;self.backend_session=None;self.last_status=None;self.error_streams=[]
        self.last_scene=None;self.scene_when=0;self.broker_ready=False;self.pending=None;self.pending_when=0
        self.last_pulse=0;self.death=False;self.stop_pending=False;self.confirmation=None
        self.request_lock=threading.RLock();self.request_revision=0;self.running_revision=None
        self.wanted=False;self.wanted_pid=None;self.monitor_token=None;self.restore_confirmed=True
        self.audit_stream=None;self.last_wait_report=0.;self.last_evidence_at=None;self.historical_request_time=None
        self.first_pending=False;self.target_addresses=[];self.death_command_id=None
        self.diagnostic=False;self.diagnostic_until=None;self.diagnostic_scope='game_all_network'
        self.probe_stream=None;self.probe_path=None;self.probe_scan_at=0.;self.death_wall=None
        self.online_window_start=None;self.online_window_wall=None;self.online_window_reported=False
        self.status_read_error_since=None;self.status_read_last_event=0.;self.death_retry_at=0.;self.pending_warn_at=0.
        self.deployment_warning=False
        self.resources_prepared=False;self.prepare_command_id=None
        self.candidate_id=None;self.candidate_until=None;self.last_end_hint=None
        self.hold_mode='ARMED';self.revoke_id=None;self.end_lease=None;self.normal_observations=[]
        self.end_latched_at=None;self.released_end_keys=set();self.end_key=None
        self.recovery_options=None;self.recover_at=None;self.recovery_count=0
        self.pulse_error_since=None;self.pulse_retry_reported=0.
        self.broker_process=None
        self.broker_failed=False
        self.thread=threading.Thread(target=self.run,daemon=True,name='FenceNetworkController');self.thread.start()

    def set_enabled(self, enabled, seconds=DEFAULT_SECONDS, local_pve=False,pid=None,monitor_token=None,combat_confirmed=False,diagnostic=False,diagnostic_scope='game_all_network'):
        with self.request_lock:
            self.request_revision+=1;revision=self.request_revision;self.wanted=bool(enabled)
            self.recovery_options=None;self.recover_at=None;self.recovery_count=0
            if enabled:self.wanted_pid=pid;self.monitor_token=monitor_token
            # Invalidate first, then signal an existing broker without waiting for log/DNS work.
            if not enabled and self.folder:
                try:self.command('stop',reason='manual_monitor_stop')
                except OSError:pass
            self.commands.put(('enable' if enabled else 'disable',dict(revision=revision,seconds=seconds,
                local_pve=local_pve,pid=pid,monitor_token=monitor_token,combat_confirmed=combat_confirmed,diagnostic=bool(diagnostic),diagnostic_scope=diagnostic_scope)))
            self.wake_work()
            return revision
    def request_current(self):
        with self.request_lock:return self.wanted and self.running_revision==self.request_revision
    def queue_session_event(self,action,monitor_token=None,**details):
        with self.request_lock:
            if self.wanted and (monitor_token is None or monitor_token==self.monitor_token):
                self.commands.put((action,dict(revision=self.request_revision,**details)));self.wake_work()
    def scene(self, settled=False, resumed=False,monitor_token=None):
        self.queue_session_event('scene',monitor_token,settled=settled,resumed=resumed)

    def handle_scene(self,data):
        # Combat, extraction and transfer remain online in death-only mode.
        return
    def normal_observation(self,monitor_token=None,**evidence):
        self.queue_session_event('normal_observation',monitor_token,evidence=dict(evidence))

    def handle_normal_observation(self,evidence):
        # A timer, transfer hint or one recovered HUD cannot revoke a hold.
        now=time.perf_counter();seen=evidence.get('observed_at',-100.)
        if not isinstance(seen,(float,int)) or not 0<=now-seen<=.65:return
        if self.hold_mode!='END_LATCHED' or self.revoke_id:return
        if self.end_latched_at is None or seen<self.end_latched_at:return
        if not isinstance(evidence.get('epoch'),int) or not isinstance(evidence.get('sequence'),int):return
        proof=evidence.get('verified_transfer') is True
        if evidence.get('full_review') is True and evidence.get('full_hud') is True:
            item=(evidence.get('epoch'),evidence.get('sequence'),seen)
            prior=self.normal_observations
            if prior and (prior[-1][0]!=item[0] or item[1]<=prior[-1][1] or not 0<seen-prior[-1][2]<=.45):prior=[]
            prior=(prior+[item])[-12:];self.normal_observations=prior
            proof=proof or (len(prior)>=3 and seen-prior[0][2]>=.2)
        else:self.normal_observations=[]
        if proof and self.end_lease:
            self.revoke_id=self.command('revoke_end_hold',hold_command_id=self.death_command_id,
                isolation_command_id=self.end_lease,reason='verified_transfer' if evidence.get('verified_transfer') else 'stable_full_hud')
            self.event('network_candidate_end_revoke_requested',command_id=self.revoke_id,hold_command_id=self.death_command_id,
                isolation_command_id=self.end_lease,evidence=evidence)
    def candidate_update(self, evidence, monitor_token=None):
        self.queue_session_event('candidate',monitor_token,evidence=dict(evidence),queued_perf=time.perf_counter())

    def end_request(self, hint, monitor_token=None):
        # Watch pins game startup/session; never accepts a payload supplied by UI.
        self.queue_session_event('end_request',monitor_token,hint=dict(hint))

    def handle_end_request(self,hint):
        if self.diagnostic or hint.get('pid')!=self.pid:return
        if not 0<=time.perf_counter()-hint.get('seen_monotonic',-100)<=1.:return
        key=(hint.get('session'),hint.get('request_id'))
        if key in self.released_end_keys:return
        self.last_end_hint=hint
        if self.candidate_id and not self.death:
            self.event('network_candidate_end_hold',pid=self.pid,request_id=hint['request_id'],
                       candidate_id=self.candidate_id,source_timestamp=hint.get('source_timestamp'))
            # Network latch only: this does not authorize game exit.
            self.hold_mode='END_LATCHED';self.death=True;self.policy.hold=True
            self.end_latched_at=hint['seen_monotonic'];self.end_key=key
            self.policy.state='death_pending';self.normal_observations=[]
            self.retain_after_death()

    def handle_candidate(self,evidence):
        if self.diagnostic or self.death:return
        action=evidence.get('action')
        if action=='cancel':
            if self.candidate_id:
                self.restore('candidate_cancelled:'+str(evidence.get('reason','recovered')))
            return
        if action!='isolate' or self.candidate_id or not self.broker_ready:return
        if self.pending and self.pending!=self.prepare_command_id:return # Never overwrite a restore awaiting ACK.
        if not 0<=time.perf_counter()-evidence.get('candidate_monotonic',-100)<=.65:return
        if not self.request_current() or self.policy.state not in ('armed','wait_online'):return
        self.candidate_until=time.monotonic()+2.
        self.policy.deadline=self.candidate_until;self.policy.state='starting'
        signal_wall=time.time_ns()
        self.candidate_id=self.command('isolate',scope='game_all_network',seconds=2.,addresses=[],
            diagnostic=False,reason='trusted_visual_candidate')
        if not self.candidate_id:self.policy.state='armed';self.policy.deadline=None;return
        self.event('network_candidate_requested',pid=self.pid,candidate_id=self.candidate_id,
                   evidence=evidence,signal_wall_ns=signal_wall,seconds=2.)
        self.tell('死亡候选 · 临时隔离游戏及已核验UU进程联网，继续确认')
        if self.last_end_hint:self.handle_end_request(self.last_end_hint)

    def wake_work(self):
        wake=getattr(self,'work_wake',None)
        if wake:wake.set()

    def prepare_candidate(self,monitor_token=None,evidence=None):
        # Preparation only. No rules or disk work on the observation thread.
        if self.wanted and not self.diagnostic and not self.death and (monitor_token is None or monitor_token==self.monitor_token):
            self.commands.put(('prepare',dict(revision=self.request_revision,evidence=evidence)));self.wake_work()

    def request_preparation(self,reason):
        if not self.broker_ready or self.diagnostic or self.death or self.candidate_id or not self.request_current() or self.prepare_command_id:return
        command_id=self.command('prepare',scope=self.diagnostic_scope,reason=reason)
        if not command_id:return
        self.prepare_command_id=command_id
        self.event('network_prepare_requested',reason=reason,pid=self.pid,already_prepared=self.resources_prepared)

    def create_broker_wake(self):
        self.close_broker_wake()
        k=C.WinDLL('kernel32',use_last_error=True)
        k.CreateEventW.argtypes=[C.c_void_p,W.BOOL,W.BOOL,W.LPCWSTR];k.CreateEventW.restype=W.HANDLE
        k.SetEvent.argtypes=[W.HANDLE];k.CloseHandle.argtypes=[W.HANDLE]
        handle=k.CreateEventW(None,False,False,'Local\\ID486NetworkWake_'+self.folder.name)
        self.broker_wake=(k,handle) if handle else None

    def notify_broker(self):
        wake=getattr(self,'broker_wake',None)
        if wake:wake[0].SetEvent(wake[1])

    def close_broker_wake(self):
        wake=getattr(self,'broker_wake',None);self.broker_wake=None
        if wake:wake[0].CloseHandle(wake[1])

    def death_detected(self, exit_enabled,monitor_token=None):
        # Only enqueue here: no locks, disk I/O, logs or acknowledgements on
        # the visual/termination thread. The controller performs network work.
        if self.wanted and not self.diagnostic and (monitor_token is None or monitor_token==self.monitor_token):
            self.commands.put(('death',dict(revision=self.request_revision,
                exit_enabled=exit_enabled,signal_monotonic_ns=time.monotonic_ns(),
                signal_wall_ns=time.time_ns())));self.wake_work()

    def retain_after_death(self):
        if self.death and self.broker_ready and not self.death_command_id and self.request_current():
            action='end_hold' if self.hold_mode=='END_LATCHED' else 'death_cutoff'
            self.command(action,reason='end_request_candidate' if action=='end_hold' else 'death_priority',scope=self.diagnostic_scope)
            self.death_command_id=self.pending
            self.tell('结束请求保持 · 正在隔离并等待视觉复核' if self.hold_mode=='END_LATCHED' else '已判定死亡 · 正在优先隔离，不等待保活')

    def handle_death(self,exit_enabled):
        if self.hold_mode=='CONFIRMED_DEATH' or self.diagnostic:return
        previous=self.policy.state
        self.hold_mode='CONFIRMED_DEATH';self.death_command_id=None;self.revoke_id=None;self.end_lease=None
        self.death=True;self.policy.hold=True;self.first_pending=False;self.confirmation=None
        self.death_wall=time.time()
        self.policy.state='death_pending'
        self.retain_after_death()
        self.event('network_death',exit_enabled=exit_enabled,isolated=previous=='isolated',
            phase=previous,requested_hold_until_game_or_assistant_exit=True,
            signal=getattr(self,'death_signal',None),urgent_monotonic_ns=time.monotonic_ns())
    def exited(self,pid=None):
        with self.request_lock:
            if pid is None or pid==self.wanted_pid:self.queue_session_event('exited')
    def publish(self,text):
        self.status(dict(text=text,revision=self.running_revision,enabled=self.enabled,
                         phase=self.policy.state,restore_confirmed=self.restore_confirmed))
    def tell(self,text):
        if text!=self.last_status:
            self.last_status=text;self.publish(text)
            self.event('network_state',status=text,phase=self.policy.state,pid=self.pid,request_revision=self.running_revision)

    def command(self, action, **details):
        with self.request_lock:
            if not self.folder:return
            if action in ('isolate','death_hold','death_cutoff','end_hold','revoke_end_hold','prepare') and not self.request_current():return
            if self.death and action in ('isolate','restore','prepare'):return
            item=dict(id=uuid.uuid4().hex,action=action,created=datetime.now(timezone.utc).isoformat(),**details)
            filename='urgent_command' if action in ('death_cutoff','end_hold') else 'command'
            temporary=self.folder/(filename+'.tmp');temporary.write_text(json.dumps(item),encoding='utf-8');os.replace(temporary,self.folder/(filename+'.json'));self.notify_broker()
            self.pending=item['id'];self.pending_when=time.monotonic()
            self.event('network_command',action=action,command_id=item['id'],pid=self.pid,request_revision=self.running_revision,
                requested_revision=self.request_revision,
                domain=HOST,domains=list(BACKEND_HOSTS),seconds=details.get('seconds'),target_ips=details.get('addresses'),reason=details.get('reason'),
                scope=details.get('scope'),diagnostic=details.get('diagnostic'),
                meaning='written_to_broker_not_proof_of_packet_blocking')
            return item['id']

    def start_broker(self):
        helper=self.assets/'network'/'ID486NetworkBroker.exe'
        if not helper.is_file():raise OSError('联网助手缺失')
        self.folder=self.base/'network_runtime'/uuid.uuid4().hex;self.folder.mkdir(parents=True)
        self.pulse_error_since=None
        self.pulse();self.create_broker_wake()
        self.resources_prepared=False;self.prepare_command_id=None
        shell=C.WinDLL('shell32',use_last_error=True)
        class ExecuteInfo(C.Structure):
            _fields_=[('cbSize',W.DWORD),('fMask',W.ULONG),('hwnd',W.HWND),('lpVerb',W.LPCWSTR),
                ('lpFile',W.LPCWSTR),('lpParameters',W.LPCWSTR),('lpDirectory',W.LPCWSTR),('nShow',C.c_int),
                ('hInstApp',W.HINSTANCE),('lpIDList',C.c_void_p),('lpClass',W.LPCWSTR),('hkeyClass',W.HKEY),
                ('dwHotKey',W.DWORD),('hIcon',W.HANDLE),('hProcess',W.HANDLE)]
        shell.ShellExecuteExW.argtypes=[C.POINTER(ExecuteInfo)];shell.ShellExecuteExW.restype=W.BOOL
        args='"'+str(self.folder)+'" '+str(os.getpid())+' '+str(self.pid)
        launch=ExecuteInfo();launch.cbSize=C.sizeof(launch);launch.fMask=0x40|0x400
        launch.lpVerb='runas';launch.lpFile=str(helper);launch.lpParameters=args;launch.lpDirectory=str(helper.parent);launch.nShow=0
        if not shell.ShellExecuteExW(C.byref(launch)) or not launch.hProcess:
            raise OSError('未启动联网助手（可能取消了管理员授权）')
        self.broker_process=launch.hProcess
        self.broker_failed=False
        self.broker_ready=False;self.broker_serial=None;self.started=time.monotonic();self.last_wait_report=0

    def pulse(self):
        if self.folder:
            now=time.monotonic()
            try:
                temporary=self.folder/'pulse.tmp';temporary.write_text(str(time.monotonic_ns()),encoding='ascii');os.replace(temporary,self.folder/'pulse')
                self.pulse_error_since=None
            except PermissionError as exc:
                # A scanner/reader may briefly deny rename. Keep the authorized
                # session alive and retry; do not permanently turn protection off.
                since=getattr(self,'pulse_error_since',None)
                if since is None:self.pulse_error_since=now;since=now
                if now-getattr(self,'pulse_retry_reported',0.)>=1.:
                    self.pulse_retry_reported=now
                    self.event('network_pulse_retry',reason=str(exc),elapsed_ms=round((now-since)*1000,3))
                if now-since>=2.:raise
                return False
            return True

    def check_broker_alive(self):
        if not self.broker_process:return
        from process_exit import K
        if K.WaitForSingleObject(self.broker_process,0)!=258:
            self.broker_failed=True;self.broker_ready=False
            self.policy.state='death_unprotected' if self.death else 'broker_unavailable'
            raise OSError('联网助手进程已退出，隔离不再有效')

    def restore(self,reason,hold=False):
        with self.request_lock:self._restore(reason,hold)

    def _restore(self,reason,hold=False):
        if self.death:return # Only explicit stop/process exit may clear a death latch.
        self.confirmation=None;self.first_pending=False
        if self.candidate_id:
            self.event('network_candidate_cancelled',pid=self.pid,reason=reason,candidate_id=self.candidate_id)
        self.candidate_id=None;self.candidate_until=None
        self.policy.restore(time.monotonic(),hold)
        if self.broker_ready:self.command('restore',reason=reason)
        self.event('network_restore',reason=reason,pid=self.pid)
        self.tell('正在解除隔离，等待游戏联网确认')

    def begin(self,options):
        self.disable('重新绑定游戏')
        self.running_revision=options['revision'];self.last_status=None
        if not self.request_current():return
        if not options['local_pve']:raise ValueError('请先在联网设置中确认当前为本地PvE战局')
        diagnostic=options.get('diagnostic') is True
        scope=options.get('diagnostic_scope','game_all_network')
        if scope not in ('backend_domains','game_external_control','game_all_network') or (scope=='game_external_control' and not diagnostic):raise ValueError('未知网络隔离方式')
        if not options.get('pid') or not (options.get('combat_confirmed') or diagnostic):raise ValueError('联网控制必须由手动快捷键或独立验证按钮启动')
        self.diagnostic=diagnostic;self.diagnostic_until=None;self.diagnostic_scope=scope
        self.policy=CyclePolicy(15 if diagnostic else options['seconds']);self.pid=int(options['pid'])
        from process_exit import GameProcess
        self.game=GameProcess(self.pid)
        if not self.game.handle:raise OSError('游戏进程身份核实失败')
        executable=process_path(self.game.handle)
        self.logs=None # Death protection never reads game logs.
        self.enabled=True;self.death=False;self.isolation_id=None;self.target_addresses=[];self.death_command_id=None
        self.death_wall=None
        self.candidate_id=None;self.candidate_until=None;self.last_end_hint=None
        self.hold_mode='ARMED';self.revoke_id=None;self.end_lease=None;self.normal_observations=[]
        self.end_latched_at=None;self.released_end_keys=set();self.end_key=None
        self.online_window_start=None;self.online_window_wall=None;self.online_window_reported=False
        self.status_read_error_since=None;self.status_read_last_event=0.;self.death_retry_at=0.;self.pending_warn_at=0.
        self.deployment_warning=False
        self.stop_pending=False;self.evidence=BackendEvidence();self.backend_session=None;self.last_scene=None;self.confirmation=None
        self.last_evidence_at=None;self.historical_request_time=None;self.last_wait_report=0
        self.policy.enable(time.monotonic());self.policy.hold=False;self.first_pending=diagnostic
        if not diagnostic:self.policy.state='armed'
        if not self.request_current():self.disable('手动监控已停止');return
        self.start_broker()
        if not self.request_current():self.disable('手动监控已停止');return
        if not diagnostic:self.recovery_options=dict(options)
        self.tell('独立验证已开始 · 准备15秒屏蔽，不执行大退' if diagnostic else '死亡断网保险待命 · 平时保持联网')

    def start_first_isolation(self):
        if not self.first_pending or not self.request_current() or not self.broker_ready or self.pending or self.policy.hold:return
        addresses=[] if self.diagnostic_scope in ('game_external_control','game_all_network') else resolve_targets()
        # Diagnose the current route, but an idle/closed backend socket must not
        # become another first-start wait. Rules also govern future connections.
        connection_error=None
        observed=[]
        if self.diagnostic_scope=='backend_domains':
            try:observed=sorted(connected_addresses(self.pid))
            except OSError as exc:connection_error=str(exc)
        with self.request_lock:
            if not self.request_current() or self.death:return
            now=time.monotonic();duration=self.policy.immediate_first(now)
            if duration is None:return
            self.target_addresses=list(addresses)
            if self.diagnostic:self.diagnostic_until=now+duration
            self.first_pending=False;self.last_remaining=None
            self.event('network_first_isolation',pid=self.pid,request_revision=self.running_revision,
                trigger='diagnostic_button' if self.diagnostic else 'manual_hotkey_hud_passed',fresh_keepalive_required=False,seconds=duration)
            self.event('network_targets',pid=self.pid,request_revision=self.running_revision,domain=HOST,
                resolved_ips=addresses,game_connection_ips=observed,matched_ips=sorted(set(observed).intersection(addresses)),
                phase='first_isolation',connection_observation_error=connection_error)
            self.command('isolate',seconds=duration,addresses=addresses,scope=self.diagnostic_scope,diagnostic=self.diagnostic)
            self.tell('首轮隔离请求已发送 · 等待助手确认规则建立')

    def handle_transport_error(self,reason):
        self.confirmation=None
        if self.policy.state in ('starting','isolated'):
            self.event('network_expected_transport_error',pid=self.pid,request_revision=self.running_revision,
                reason=reason,phase=self.policy.state,kept_until_deadline=True)
            return
        self.restore(reason,hold=self.policy.hold)

    def handle_session_error(self,reason):
        self.confirmation=None
        if self.diagnostic_scope=='game_all_network' and self.policy.state in ('starting','isolated','death_pending','death_hold'):
            self.event('network_expected_session_error',reason=reason,scope=self.diagnostic_scope,rules_retained=True)
            return
        self.restore(reason,hold=True)

    def finish_diagnostic_if_due(self,now):
        if not self.diagnostic or self.diagnostic_until is None or now<self.diagnostic_until:return False
        # Native expiry can reset policy.deadline before this thread observes it.
        with self.request_lock:self.wanted=False
        self.disable('15秒独立验证结束，已请求恢复联网')
        self.event('network_diagnostic_finished',restore_confirmed=self.restore_confirmed,seconds=15)
        return True

    def disable(self,reason):
        self.candidate_id=None;self.candidate_until=None;self.last_end_hint=None
        self.hold_mode='ARMED';self.revoke_id=None;self.end_lease=None;self.normal_observations=[]
        self.end_latched_at=None;self.released_end_keys=set();self.end_key=None
        self.recovery_options=None;self.recover_at=None
        restore_confirmed=not self.broker_ready
        if self.folder:
            try:
                self.command('stop',reason=reason)
                if self.broker_ready:
                    until=time.monotonic()+2
                    while time.monotonic()<until:
                        try:
                            data=read_snapshot(self.folder/'status.json')
                            if data.get('state')=='online' and data.get('filter_count')==0 and (data.get('command_id')==self.pending or data.get('reason') in ('game_or_parent_exited','user_stop')):
                                self.event('network_restore_ack',pid=self.pid,request_revision=self.running_revision,
                                    command_id=data.get('command_id'),broker_timestamp=data.get('timestamp'),filter_count=0,
                                    restore_confirmed=True,reason=data.get('reason'))
                                restore_confirmed=True;break
                        except (OSError,ValueError):pass
                        time.sleep(.05)
            except OSError:pass # Broker also restores after four seconds without parent heartbeats.
        self.restore_confirmed=restore_confirmed
        self.drain_broker_events(maximum=4096)
        if self.audit_stream:self.audit_stream.close();self.audit_stream=None
        self.enabled=False;self.policy.disable();self.broker_ready=False;self.first_pending=False;self.diagnostic=False;self.diagnostic_until=None;self.diagnostic_scope='game_all_network'
        if self.stream:self.stream.close();self.stream=None
        if self.probe_stream:self.probe_stream.close();self.probe_stream=None
        self.probe_path=None;self.probe_scan_at=0.
        for stream in self.error_streams:stream.close()
        self.error_streams=[]
        self.backend=None;self.backend_session=None;self.confirmation=None
        if self.game:self.game.close();self.game=None
        if self.broker_process:
            from process_exit import K
            K.CloseHandle(self.broker_process);self.broker_process=None
        self.broker_failed=False
        self.close_broker_wake();self.resources_prepared=False;self.prepare_command_id=None
        self.folder=None;self.pending=None
        if not restore_confirmed:self.tell('联网控制已停止 · 恢复尚未确认，独立助手将限时清理')
        else:self.tell('联网控制已停止 · '+reason)

    def tail(self,now):
        if now-self.scan_at>=1:
            self.scan_at=now
            directories=sorted((d for d in self.logs.iterdir() if d.is_dir() and d.name.startswith('log_')),key=lambda p:p.stat().st_mtime,reverse=True)
            path=select_backend_log(directories[0]) if directories else None
            if path and path!=self.backend:
                changed=self.backend is not None
                if self.stream:self.stream.close()
                self.stream=path.open('r',encoding='utf-8',errors='replace');self.stream.seek(0,2)
                self.backend=path;self.evidence=BackendEvidence()
                self.event('network_log_source',pid=self.pid,request_revision=self.running_revision,
                    path=str(path),skip_existing_records=True,excluded='backend_queue*.log')
                for stream in self.error_streams:stream.close()
                self.error_streams=[]
                for error_path in path.parent.glob('*errors*.log'):
                    stream=error_path.open('r',encoding='utf-8',errors='replace');stream.seek(0,2);self.error_streams.append(stream)
                # Historical samples estimate scheduling only; they NEVER authorize a new lease.
                with path.open('rb') as history:
                    history.seek(max(0,path.stat().st_size-320000));text=history.read().decode('utf-8',errors='replace')
                times=[]
                for entry in text.splitlines():
                    if backend_host(entry) and ROUTE in entry and 'Request HTTPS' in entry:
                        try:times.append(datetime.strptime(entry[:23],'%Y-%m-%d %H:%M:%S.%f').timestamp())
                        except ValueError:pass
                gaps=[b-a for a,b in zip(times,times[1:])][-3:]
                if len(gaps)>=2 and all(30<=g<=600 for g in gaps) and max(gaps)-min(gaps)<5:
                    self.policy.period=min(gaps)
                self.historical_request_time=times[-1] if times else None
                if changed:self.handle_session_error('游戏日志会话已改变')
        for stream in self.error_streams:
            for _ in range(100):
                line=stream.readline()
                if not line:break
                if session_error(line):self.handle_session_error('游戏错误日志报告会话失败')
                elif ROUTE in line and response_error(line):self.handle_transport_error('游戏保活连接失败')
        if not self.stream:return
        for _ in range(100):
            position=self.stream.tell()
            line=self.stream.readline()
            if not line:break
            if not line.endswith('\n'):
                self.stream.seek(position);break
            if session_error(line):
                self.handle_session_error('游戏会话认证失败')
            if backend_host(line) and ROUTE in line and 'Request HTTPS' in line and '<---' not in line:
                try:self.historical_request_time=datetime.strptime(line[:23],'%Y-%m-%d %H:%M:%S.%f').timestamp()
                except ValueError:pass
            result=self.evidence.parse(line,now)
            if not result:continue
            self.last_evidence_at=now
            self.event('network_evidence',evidence_kind=result['kind'],pid=self.pid,host=result.get('host',HOST),route=ROUTE)
            if result['kind']=='failed':
                if session_error(line):self.handle_session_error('游戏会话请求失败')
                else:self.handle_transport_error('游戏保活通信请求失败')
            elif self.policy.state=='wait_online':
                if self.policy.hold:
                    self.tell('已收到保活响应，但网控仍暂停 · 请检查暂停原因或手动重新开启')
                    continue
                # Drain available error/backend records before verify_online.
                # No fixed 3-second sleep or transient TCP-table presence gate.
                if self.online_window_wall is not None:
                    try:response_wall=datetime.strptime(line[:23],'%Y-%m-%d %H:%M:%S.%f').timestamp()
                    except ValueError:continue
                    if response_wall<=self.online_window_wall:continue
                self.confirmation=dict(result,verify_after=now)
                self.tell('收到当前游戏恢复后的保活响应 · 准备下一轮隔离')

    def record_backend_probe(self,now):
        """Observe client log metadata only. Never treat Response as server success."""
        try:
            if now-self.probe_scan_at>=3:
                self.probe_scan_at=now
                folders=sorted((p for p in self.logs.iterdir() if p.is_dir() and p.name.startswith('log_')),key=lambda p:p.stat().st_mtime,reverse=True)
                path=select_backend_log(folders[0]) if folders else None
                if path and path!=self.probe_path:
                    if self.probe_stream:self.probe_stream.close()
                    self.probe_stream=path.open('r',encoding='utf-8',errors='replace');self.probe_stream.seek(0,2);self.probe_path=path
            if not self.probe_stream:return
            for _ in range(200):
                position=self.probe_stream.tell();line=self.probe_stream.readline()
                if not line:break
                if not line.endswith('\n'):self.probe_stream.seek(position);break
                if not (self.diagnostic or self.death):continue
                match=re.search(r'\b(?:https?|wss?)://([A-Za-z0-9.-]+)(/[^\s,?]*)?',line)
                if not match:continue
                host=match.group(1).lower().rstrip('.')
                if not vendor_host(host):continue
                if self.death:
                    try:logged=datetime.strptime(line[:23],'%Y-%m-%d %H:%M:%S.%f').timestamp()
                    except ValueError:continue
                    if self.death_wall is not None and logged<self.death_wall:continue
                route=(match.group(2) or '/').rstrip('.')
                route=re.sub(r'(?i)(?<=/)[a-f0-9-]{16,}(?=/|$)','<id>',route)
                ident=re.search(r'id\s*\[(\d+)\]',line)
                self.event('network_backend_probe',client_timestamp=line[:23],host=host,route=route,
                    direction='response' if '<---' in line else 'request',request_id=ident.group(1) if ident else None,
                    protocol_status=http_status(line),phase=self.policy.state,diagnostic=self.diagnostic,
                    death_hold_requested=self.death,meaning='client_log_metadata_not_proof_of_server_success')
        except OSError as exc:
            self.event('network_probe_error',reason=str(exc),rules_unchanged=True)
            if self.probe_stream:self.probe_stream.close();self.probe_stream=None

    def schedule_keepalive_window(self,now):
        with self.request_lock:
            if self.death or self.policy.hold or self.policy.state!='isolated' or self.policy.deadline is None:return
            period=self.policy.period;last=self.historical_request_time
            if period is None or last is None or not 30<=period<=600:return
            wall=time.time();next_request=last+period
            if next_request<=wall:next_request+=period*(math.floor((wall-next_request)/period)+1)
            planned=now+(next_request-wall)-2
            # Align to the next expected game heartbeat when possible, but NEVER
            # extend the user's cap or the broker's independent hard deadline.
            if now+.2<planned<self.policy.deadline:
                self.policy.deadline=planned
                self.event('network_restore_scheduled',seconds_until_restore=round(planned-now,3),
                    measured_period_seconds=period,lead_seconds=2,lease_extended=False)

    def verify_online(self,now):
        with self.request_lock:self._verify_online(now)

    def _verify_online(self,now):
        candidate=self.confirmation
        if not candidate or now<candidate['verify_after']:return
        self.confirmation=None
        if not self.request_current() or not self.broker_ready or self.pending or self.policy.state!='wait_online' or self.policy.hold:return
        addresses=list(self.target_addresses)
        if not addresses and self.diagnostic_scope=='backend_domains':
            self.tell('缺少本会话已核实的隔离地址 · 保持联网');return
        if not self.request_current() or self.death:return
        self.event('network_targets',pid=self.pid,request_revision=self.running_revision,domain=HOST,
            resolved_ips=addresses,phase='current_session_verified_target_cache')
        # This proves current game-to-backend communication, NOT authentication,
        # successful settlement or rollback. Do not label an empty response 200.
        self.event('network_evidence',evidence_kind='current_game_paired_response',pid=self.pid,
                   authentication_verified=False,protocol_success_explicit=candidate['kind']=='confirmed',error_observation_seconds=0)
        duration=self.policy.confirmed(candidate['request'],candidate['response'])
        if duration:
            self.last_remaining=None
            self.command('isolate',seconds=duration,addresses=addresses,scope=self.diagnostic_scope,diagnostic=False)
            self.tell('游戏联网通信已核实 · 正在建立限时隔离')
        elif self.policy.period is None:self.tell('游戏联网通信已核实 · 等待下一轮以确认保活周期')

    def broker_status(self,now):
        with self.request_lock:self._broker_status(now)

    def _broker_status(self,now):
        if not self.folder:return
        status=self.folder/'status.json'
        if not status.is_file():
            if self.broker_ready:
                if self.status_read_error_since is None:self.status_read_error_since=now
                if self.death or now-self.status_read_error_since<3:
                    if now-self.status_read_last_event>=2:
                        self.status_read_last_event=now;self.event('network_status_read_retry',reason='status_temporarily_missing',death_hold_requested=self.death)
                    return
                raise OSError('联网助手状态持续缺失，详情见日志')
            if now-self.started>30:raise OSError('联网助手未就绪，保持联网')
            return
        try:data=read_snapshot(status)
        except (OSError,ValueError) as exc:
            if self.status_read_error_since is None:self.status_read_error_since=now
            if now-self.status_read_last_event>=2:
                self.status_read_last_event=now
                self.event('network_status_read_retry',reason=str(exc),path=str(status),death_hold_requested=self.death)
            if self.death:
                self.tell('死亡隔离状态暂不可读 · 未请求恢复，详情见日志');return
            if now-self.status_read_error_since<3:return
            raise OSError('联网状态持续不可读，详情见日志') from exc
        self.status_read_error_since=None
        serial=data.get('serial')
        if serial==getattr(self,'broker_serial',None):return
        self.broker_serial=serial;self.event('network_broker',**data)
        if data.get('command_id')==getattr(self,'prepare_command_id',None) and getattr(self,'prepare_command_id',None):
            self.resources_prepared=bool(data.get('resources_prepared'));self.prepare_command_id=None
            self.event('network_prepare_ack',prepared=self.resources_prepared,broker_timestamp=data.get('timestamp'),filter_count=data.get('filter_count'),reason=data.get('reason'))
        if data['state']=='error':raise OSError(data.get('reason','联网助手异常'))
        if data['state']=='online':
            self.broker_ready=True
            if self.hold_mode=='END_LATCHED' and self.revoke_id and data.get('command_id')==self.revoke_id and data.get('reason')=='end_hold_revoked':
                self.event('network_candidate_end_revoke_ack',command_id=self.revoke_id,broker_timestamp=data.get('timestamp'))
                self.death=False;self.hold_mode='ARMED';self.death_command_id=None;self.revoke_id=None;self.end_lease=None
                self.candidate_id=None;self.candidate_until=None;self.last_end_hint=None;self.pending=None
                self.policy.hold=False;self.policy.state='armed';self.policy.deadline=None
                self.normal_observations=[];self.isolation_id=None;self.resources_prepared=False
                if len(self.released_end_keys)>128:self.released_end_keys.clear()
                self.released_end_keys.add(self.end_key);self.end_key=None;self.end_latched_at=None
            # A delayed preparation-only acknowledgement must not reset a death latch.
            if self.death and data.get('reason') in ('ready','resources_prepared','prepare_failed') and data.get('command_id')!=self.death_command_id:
                self.retain_after_death();return
            if self.candidate_id and data.get('command_id')!=self.candidate_id:
                return
            if self.candidate_id and data.get('reason') not in ('ready','resources_prepared','prepare_failed'):
                self.event('network_candidate_released',pid=self.pid,candidate_id=self.candidate_id,reason=data.get('reason'))
                self.candidate_id=None;self.candidate_until=None
            matched=data.get('command_id')==self.pending
            if matched:self.pending=None
            if self.policy.state in ('isolated','starting','death_hold'):self.policy.restore(now,self.policy.hold)
            self.isolation_id=None
            if self.death:
                if data.get('command_id')==self.death_command_id:
                    self.policy.state='death_unprotected'
                    self.tell('死亡隔离尚未确认 · 详情见日志')
                if data.get('reason')!='death_cutoff_no_verified_targets' and now-self.death_retry_at>=1:
                    self.death_retry_at=now;self.death_command_id=None
                    self.event('network_death_hold_lost',broker_reason=data.get('reason'),next_action='retry_death_cutoff')
                    self.retain_after_death()
                elif not self.death_command_id:self.retain_after_death()
                return
            if not self.diagnostic:
                self.policy.state='armed'
                if not self.resources_prepared and not self.pending and data.get('reason') not in ('resources_prepared','prepare_failed'):self.request_preparation('armed_ready')
                if data.get('reason')=='prepare_failed':
                    self.event('network_prepare_error',reason='broker_prepare_failed',blocking=False)
                    self.tell('预备未确认 · 平时保持联网')
                else:self.tell('死亡断网保险已预备 · 平时保持联网' if self.resources_prepared else '死亡断网保险就绪 · 平时保持联网')
                return
            if data.get('reason')!='ready' and self.online_window_start is None:
                self.online_window_start=now;self.online_window_reported=False
                try:self.online_window_wall=datetime.fromisoformat(data['timestamp']).timestamp()
                except (KeyError,ValueError):self.online_window_wall=time.time()
                self.policy.online_after=now
                self.event('network_reconnect_started',broker_timestamp=data.get('timestamp'),timeout_seconds=RECONNECT_TIMEOUT)
            self.tell(('联网助手就绪 · 正在启动首轮隔离' if self.first_pending else '联网助手就绪 · 尚未隔离')
                if data.get('reason')=='ready' else '已解除隔离 · 等待本轮游戏联网确认')
        elif data['state']=='isolated' and data.get('command_id')==self.pending:
            if self.death:return # Wait for terminal death_hold acknowledgement.
            self.pending=None;self.isolation_id=data.get('isolation_command_id')
            if self.online_window_start is not None:
                self.event('network_reconnect_complete',observed_online_ms=round((now-self.online_window_start)*1000,3),
                    online_ms=self.reconnect_elapsed_ms(data),completion='cycle_reisolated',
                    broker_timestamp=data.get('timestamp'),meaning='rule_window_not_proof_of_server_authentication')
                self.online_window_start=None;self.online_window_wall=None
            policy=data.get('policy') or {};self.deployment_warning=not policy.get('coverage_complete',False)
            self.event('network_policy_status',policy=policy,full_coverage_verified=not self.deployment_warning)
            self.policy.isolated()
            if self.candidate_id:
                self.resources_prepared=False;self.prepare_command_id=None
                self.event('network_candidate_ack',pid=self.pid,candidate_id=self.candidate_id,
                           broker_timestamp=data.get('timestamp'),filter_count=data.get('filter_count'),
                           coverage_verified=not self.deployment_warning)
            self.tell('规则已部署，策略审计未完整确认 · 详情见日志' if self.deployment_warning else '游戏及已核验UU进程规则已部署 · '+('15秒后解除' if self.diagnostic else '包含本机连接'))
            self.retain_after_death()
        elif data['state']=='death_hold' and data.get('command_id')==self.death_command_id:
            self.pending=None;self.policy.state='death_hold';self.policy.deadline=None
            self.end_lease=data.get('isolation_command_id') if self.hold_mode=='END_LATCHED' else None
            if self.online_window_start is not None:
                self.event('network_reconnect_complete',online_ms=self.reconnect_elapsed_ms(data),completion='death_priority_cutoff',
                    broker_timestamp=data.get('timestamp'))
                self.online_window_start=None;self.online_window_wall=None
            self.event('network_candidate_end_hold_ack' if self.hold_mode=='END_LATCHED' else 'network_death_hold_ack',pid=self.pid,isolation_command_id=data.get('isolation_command_id'),
                filter_count=data.get('filter_count'),broker_timestamp=data.get('timestamp'))
            self.deployment_warning=not (data.get('policy') or {}).get('coverage_complete',False)
            self.tell('结束请求保持 · 等待完整战斗HUD或已确认的转移反证' if self.hold_mode=='END_LATCHED' else ('死亡规则保持 · 策略审计未完整确认，详情见日志' if self.deployment_warning else '已判定死亡 · 保留隔离直到退出游戏或助手；可手动停止解除'))

    def reconnect_elapsed_ms(self,data):
        try:return round((datetime.fromisoformat(data['timestamp']).timestamp()-self.online_window_wall)*1000,3)
        except (KeyError,ValueError,TypeError):return None

    def check_reconnect_timeout(self,now):
        with self.request_lock:self._check_reconnect_timeout(now)

    def _check_reconnect_timeout(self,now):
        if self.death or self.policy.hold or self.policy.state!='wait_online' or self.online_window_start is None:return
        if now-self.online_window_start>=RECONNECT_TIMEOUT and not self.pending and not self.online_window_reported:
            self.online_window_reported=True
            self.event('network_reconnect_timeout',online_ms=round((now-self.online_window_start)*1000,3),
                next_action='continue_waiting_for_fresh_game_response_death_cutoff_enabled')
            self.tell('重连超过10秒未确认 · 当前联网，继续等待；死亡仍会优先隔离')

    def drain_broker_events(self,maximum=100):
        try:self.read_broker_events(maximum)
        except OSError as exc:
            self.event('network_audit_error',reason=str(exc),pid=self.pid,request_revision=self.running_revision)
            if self.audit_stream:
                try:self.audit_stream.close()
                except OSError:pass
            self.audit_stream=None

    def read_broker_events(self,maximum=100):
        if not self.folder:return
        path=self.folder/'events.jsonl'
        if self.audit_stream is None:
            if not path.is_file():return
            self.audit_stream=path.open('r',encoding='utf-8-sig',errors='replace')
        for _ in range(maximum):
            position=self.audit_stream.tell();line=self.audit_stream.readline()
            if not line:break
            if not line.endswith('\n'):self.audit_stream.seek(position);break
            try:record=json.loads(line)
            except ValueError:continue
            kind=record.get('event')
            event_name={'rules_committed':'network_rules','classify_drop':'network_block_hit',
                        'hit_monitor':'network_hit_monitor','hit_summary':'network_hit_summary',
                        'broker_state':'network_broker_audit','rules_removed':'network_rules_removed',
                        'resolved_targets':'network_targets_verified'}.get(kind,'network_broker_audit')
            event_name={'policy_context_created':'network_policy_context','policy_readback':'network_policy_readback',
                        'arbitration_snapshot':'network_arbitration_snapshot','policy_audit_error':'network_policy_audit_error',
                        'arbitration_audit_error':'network_policy_audit_error','policy_context_released':'network_policy_released'}.get(kind,event_name)
            self.event(event_name,pid=self.pid,request_revision=self.running_revision,
                broker_session=self.folder.name,broker_timestamp=record.get('timestamp'),
                broker_sequence=record.get('sequence'),record=record)

    def report_wait(self,now):
        if self.policy.state!='wait_online' or now-self.last_wait_report<15:return
        self.last_wait_report=now
        self.event('network_wait',pid=self.pid,request_revision=self.running_revision,
            reason='session_hold' if self.policy.hold else 'awaiting_fresh_keepalive',
            elapsed_seconds=round(max(0,now-self.policy.online_after),3),backend_path=str(self.backend) if self.backend else None,
            measured_keepalive_period=self.policy.period,last_evidence_monotonic=self.last_evidence_at,
            last_historical_request_unix=self.historical_request_time,isolated=False)

    def run(self):
        while not self.closed.is_set():
            self.work_wake.wait(.01);self.work_wake.clear()
            if self.closed.is_set():break
            try:
                for _ in range(20):
                    try:action,data=self.commands.get_nowait()
                    except queue.Empty:break
                    with self.request_lock:
                        current=data.get('revision')==self.request_revision
                    if not current:continue
                    if action=='enable':
                        if self.wanted:self.begin(data)
                    elif action=='disable':
                        self.running_revision=data['revision'];self.last_status=None
                        self.disable('手动监控已停止，已恢复联网')
                    elif not self.request_current():continue
                    elif action=='scene' and self.enabled and not self.death:
                        self.handle_scene(data)
                    elif action=='normal_observation' and self.enabled:
                        self.handle_normal_observation(data['evidence'])
                    elif action=='candidate' and self.enabled:
                        self.handle_candidate(data['evidence'])
                    elif action=='end_request' and self.enabled:
                        self.handle_end_request(data['hint'])
                    elif action=='prepare' and self.enabled and not self.death and not self.diagnostic:
                        self.event('network_prepare_candidate',pid=self.pid,evidence=data.get('evidence'))
                        self.request_preparation('suspicious_frame')
                    elif action=='death':
                        if self.enabled:
                            self.death_signal=data
                            self.handle_death(data['exit_enabled'])
                        else:
                            # Never restart a fresh online session after a death
                            # arrived while recovering the previous controller.
                            self.recovery_options=None;self.recover_at=None
                    elif action=='exited' and self.enabled:self.disable('游戏进程已退出')
                self.recover_if_due(time.monotonic())
                if not self.enabled:continue
                if not self.request_current():self.disable('旧的监控请求已取消');continue
                now=time.monotonic()
                from process_exit import K
                if not self.game or K.WaitForSingleObject(self.game.handle,0)!=258:
                    self.disable('游戏进程已退出');continue
                if self.broker_failed:continue
                self.check_broker_alive()
                if now-self.last_pulse>=.75:self.pulse();self.last_pulse=now
                self.broker_status(now)
                self.drain_broker_events()
                self.start_first_isolation()
                now=time.monotonic()
                if self.finish_diagnostic_if_due(now):continue
                if self.pending and now-self.pending_when>3:
                    if self.pending==self.prepare_command_id and not self.death:
                        self.event('network_prepare_error',reason='preparation_ack_timeout',blocking=False)
                        self.pending=None;self.prepare_command_id=None
                        continue
                    if not self.death:raise OSError('联网助手未确认命令，停止循环并恢复')
                    if now-self.pending_warn_at>=2:
                        self.pending_warn_at=now
                        self.event('network_death_ack_delayed',command_id=self.pending,elapsed_seconds=round(now-self.pending_when,3))
                        self.tell('死亡隔离确认延迟 · 未请求恢复，详情见日志')
                if not self.death and self.policy.due(now):
                    if self.diagnostic:
                        self.finish_diagnostic_if_due(now);continue
                    self.restore('本轮隔离已到期',hold=False)
                if not self.death and self.policy.state=='isolated' and self.policy.deadline is not None:
                    remaining=max(0,math.ceil(self.policy.deadline-now))
                    if remaining!=getattr(self,'last_remaining',None):
                        self.last_remaining=remaining
                        prefix='游戏全网络验证 · ' if self.diagnostic else '游戏全网络规则 · '
                        if self.deployment_warning:prefix='规则部署，审计未确认 · '
                        self.publish(prefix+str(remaining)+'秒内恢复')
                # Settlement errors/old visual events must not undo a death hold.
                # Process identity, broker acknowledgements and watchdogs still run.
            except Exception as exc:
                self.handle_fault(exc)
        self.disable('程序退出')

    def close(self):
        self.closed.set();self.wake_work();self.thread.join(timeout=2)

    def handle_fault(self,exc):
        self.event('network_error',reason=str(exc),death_hold_requested=self.death)
        if self.death and self.enabled:
            self.tell('联网助手已退出 · 当前无隔离保护，请手动停止后重启保险' if not self.broker_ready else '死亡隔离状态异常 · 未请求恢复，详情见日志')
        else:
            options=getattr(self,'recovery_options',None)
            eligible=(isinstance(exc,OSError) and options and not self.diagnostic and
                      not self.closed.is_set() and self.request_current())
            self.disable('发生异常，已停止；详情见日志')
            if eligible:
                with self.request_lock:
                    if (self.wanted and options['revision']==self.request_revision and
                        options.get('pid')==self.wanted_pid and options.get('monitor_token')==self.monitor_token):
                        self.recovery_options=options
                        self.recovery_count=getattr(self,'recovery_count',0)+1
                        delay=min(10.,2.**min(3,self.recovery_count-1))
                        self.recover_at=time.monotonic()+max(delay,0. if self.restore_confirmed else 4.5)
                        self.event('network_auto_recover_scheduled',pid=self.wanted_pid,retry_seconds=delay)
                        self.tell('断网保险正在自动恢复')

    def recover_if_due(self,now):
        options=getattr(self,'recovery_options',None);due=getattr(self,'recover_at',None)
        if due is None or now<due or not options:return
        with self.request_lock:
            if (self.closed.is_set() or not self.wanted or self.death or
                options['revision']!=self.request_revision or options.get('pid')!=self.wanted_pid or
                options.get('monitor_token')!=self.monitor_token):
                self.recovery_options=None;self.recover_at=None;return
        self.recover_at=None
        # begin() verifies the same game PID/image again; no captured frame or
        # old death command is replayed. Status updates have no sound hooks.
        self.event('network_auto_recover_start',pid=options['pid'],request_revision=options['revision'])
        self.begin(dict(options))
