"""Per-raid summary assembled from journal events (observation only; it never changes a decision).

A round starts at 'armed' (or when auto-pause standby resumes into a new raid) and ends at a death
exit, an extraction, the end of an auto-pause, the game closing while paused, or any other stop.
The summary waits after the end so the process-exit check, the network restore and the network
helper's own audit records can be included, then is written once to both logs.

Times are wall-clock local time; relative values are milliseconds after the death/extraction decision
(negative = before it).
"""
from datetime import datetime
from pathlib import Path
import re, threading, time

WAIT_AFTER_END = 10.     # longest wait for exit / network confirmations after a death exit
SETTLE = 1.5             # extra wait after the last expected confirmation (late audit records, end request)
MENU_ZERO_EARLY = .5     # raid-log menu at most this long before the last countdown's expected zero ...
MENU_ZERO_LATE = 60.     # ... and at most this long after it (loading / summary screens delay the log)
RESPONSE_WAIT = 3.       # one extra wait when the game log has the end request but not yet its response
REQUEST_AFTER_END = 3.   # an end request logged later than this after the round ended belongs to something else
RESPONSE_LIMIT = 10.     # responses are looked for up to this long after the round ended
LOG_TAIL = 2 * 1024 * 1024
END_LINE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\|[^|]*\|[A-Za-z]+\|backend\|(--->|<---) [A-Za-z]+ HTTPS, id \[(\d+)\]: URL: https://[A-Za-z0-9.-]+/client/match/local/end[,.\s?]')

_EXIT_STATES = {'exited': '已退出', 'still_running': '仍在运行', 'unavailable': '无法确认'}


def _local(ns):
    if ns is None: return None
    return datetime.fromtimestamp(ns / 1e9).astimezone().isoformat(timespec='milliseconds')


def _clock(ns):
    return '—' if ns is None else datetime.fromtimestamp(ns / 1e9).strftime('%H:%M:%S.%f')[:-3]


def _iso_ns(text):
    """Broker ISO timestamp with offset -> unix ns."""
    if not isinstance(text, str): return None
    try: return int(datetime.fromisoformat(text).timestamp() * 1e9)
    except ValueError: return None


def _game_log_ns(text):
    """Game log '2026-11-19 16:27:49.437' (local time) -> unix ns."""
    if not isinstance(text, str): return None
    try: return int(datetime.strptime(text[:23], '%Y-%m-%d %H:%M:%S.%f').timestamp() * 1e9)
    except ValueError: return None


def scan_end_requests(paths, start_ns, stop_ns):
    """Header fields of /client/match/local/end lines in the game's backend log (never bodies)."""
    found = {}
    for path in paths:
        try:
            with open(path, 'rb') as f:
                f.seek(0, 2); size = f.tell(); f.seek(max(0, size - LOG_TAIL))
                data = f.read().decode('utf-8', errors='replace')
        except OSError:
            continue
        for line in data.splitlines():
            if '/client/match/local/end' not in line: continue
            m = END_LINE.match(line)
            if not m: continue
            at = _game_log_ns(m.group(1))
            if at is None or not (start_ns <= at <= stop_ns): continue
            item = found.setdefault(m.group(3), {})
            if m.group(2) == '--->':
                item['request'] = at
            else:
                item['response'] = at
                code = re.search(r'responseCode:\s*(\d+)', line.split('responseText:', 1)[0])
                item['code'] = int(code.group(1)) if code else None
    return found


def _first_at_or_after(values, start):
    if start is None: return None
    later = [v for v in values if v[0] is not None and v[0] >= start]
    return min(later) if later else None


DEATH_BASIS = {
    'relative_death_confirmed': 'HUD消失与相对渐黑共同确认',
    'esc_death_fade_confirmed': 'ESC界面死亡渐黑',
    'settings_death_fade_confirmed': '设置界面死亡渐黑',
    'settlement_visual_confirmed': '结算日志与连续HUD消失共同确认',
    'settlement_parallel_confirmed': '结算日志与独立新画面共同确认',
}
EXTRACTION_BASIS = {
    'combat': '倒计时接近结束，战斗HUD与场景一起渐黑',
    'inventory': '倒计时接近结束，背包界面整体渐黑',
    'esc': '倒计时接近结束，ESC菜单整体渐黑',
    'settings': '倒计时接近结束，设置界面整体渐黑',
    'deadline': '倒计时归零窗口内HUD消失（归零前0.3秒以内）',
}


class RoundSummary:
    def __init__(self, wall_ns=None, mono_ns=None):
        self.lock = threading.Lock()
        self.wall0 = wall_ns if wall_ns is not None else time.time_ns()
        self.mono0 = mono_ns if mono_ns is not None else time.perf_counter_ns()
        self.round = None
        self.after_restart = False

    def _perf(self, seconds):
        """perf_counter seconds (detector/decision monotonic fields) -> unix ns."""
        if not isinstance(seconds, (int, float)): return None
        return int(self.wall0 + seconds * 1e9 - self.mono0)

    def _new(self, wall, how):
        r = dict(armed=wall, how=how, after_restart=self.after_restart, network_active=False,
                 transfers=0, zone_pauses=0, end_requests=0, last_confirm=None,
                 commits=[], drops=[], deadline=None, done_at=None, log_paths=set(), scanned=0)
        self.after_restart = False
        return r

    def observe(self, kind, d, wall):
        """Feed one journal event. Returns finished summaries (usually an empty list)."""
        d = d or {}
        with self.lock:
            out = []
            r = self.round
            if kind in ('armed', 'startup'):
                if r is not None:
                    if r.get('end') is None:
                        r['outcome'] = r.get('outcome') or ('未记录结束（再次开启监控）' if kind == 'armed' else '未记录结束（程序重新启动）')
                        r['end'] = wall
                    out.append(self._take(r))
                if kind == 'armed':
                    self.round = self._new(wall, 'restart' if self.after_restart else 'armed')
                    self.round['dry_run'] = d.get('dry_run')
                else:
                    self.after_restart = False
                return out
            if kind == 'zone_standby_resumed' and r is None:
                self.round = self._new(wall, 'resumed')
                return out
            if r is None: return out
            if r.get('end') is None: self._during(r, kind, d, wall)
            else: self._after(r, kind, d, wall)
            ready = self._ready(r, wall)
            if ready: out.append(ready)
            return out

    def _during(self, r, kind, d, wall):
        if kind in DEATH_BASIS: r['last_confirm'] = kind
        elif kind == 'death_decision':
            r['outcome'] = '死亡'; r['decision'] = d.get('decision_wall_ns') or wall
            r['death_basis'] = DEATH_BASIS.get(r['last_confirm'], 'HUD与菜单持续消失超过确认延时')
            ep = d.get('diagnostic_episode') or {}
            r['suspicious'] = self._perf(ep.get('first_reviewed_monotonic'))
        elif kind == 'trigger':
            r['end'] = wall
            if d.get('dry_run'): r['outcome'] = '死亡（演练，未退出）'
        elif kind == 'extraction_transition':
            ev = d.get('evidence') or {}
            r['outcome'] = '撤离'; r['decision'] = wall
            r['extraction_basis'] = EXTRACTION_BASIS.get(ev.get('latch_context') or ev.get('context'), ev.get('latch_policy') or ev.get('policy'))
            r['ui_vs_zero_ms'] = ev.get('ui_last_seen_vs_zero_ms')
        elif kind == 'disarmed':
            reason = str(d.get('reason') or ''); r['stop_reason'] = reason
            if 'outcome' not in r:
                r['outcome'] = ('撤离' if '撤离渐黑' in reason else '程序退出' if '程序退出' in reason else
                                '手动停止' if ('手动' in reason or 'F10' in reason) else
                                '在线战局，已拒绝' if 'Online' in reason else
                                '撤离未能确认，已停止（未退出）' if '未能确认' in reason else
                                '错误停止' if '错误' in reason else '停止')
            r['end'] = wall
            if '程序退出' in reason: r['flush_now'] = True
        elif kind == 'zone_done':
            confirmed = d.get('confirmed', True)
            r['outcome'] = '撤离/转移（自动暂停）' if confirmed else '未确认撤离（自动暂停期间结束，可能死亡）'
            r['decision'] = wall; r['zone_remaining_s'] = d.get('remaining_s'); r['end'] = wall
        elif kind == 'zone_game_exited':
            r['outcome'] = '游戏已关闭（暂停判断期间）'; r['end'] = wall; self.after_restart = True
        elif kind == 'center_extraction_countdown':
            v = d.get('value')
            if isinstance(v, (int, float)): r['last_countdown'] = (float(v), d.get('frame_wall_ns') or wall)
        elif kind == 'zone_standby_started' and d.get('reason') == 'raid_log_menu' and 'outcome' not in r:
            # The game log says the raid is over and the game is back at the menu, with no death or
            # extraction judged on screen (e.g. focus was away for the last seconds of a countdown).
            # Call it an extraction only if a countdown read in this round should have reached zero
            # before the menu came back; the game log alone does not say extraction or death.
            lc = r.get('last_countdown'); r['end'] = wall; r['decision'] = wall
            zero = None if lc is None else lc[1] + int(lc[0] * 1e9)
            if zero is not None and zero - int(MENU_ZERO_EARLY * 1e9) <= wall <= zero + int(MENU_ZERO_LATE * 1e9):
                r['outcome'] = '撤离（游戏日志确认回到菜单）'
                r['extraction_basis'] = f'倒计时 {lc[0]:.1f} 秒读数预计已归零，随后游戏日志回到菜单'
            else:
                r['outcome'] = '本局已结束（游戏日志回到菜单，画面未判定死亡或撤离）'
        elif kind == 'transfer_loading': r['transfers'] += 1
        elif kind == 'zone_entered': r['zone_pauses'] += 1
        self._common(r, kind, d, wall)

    def _after(self, r, kind, d, wall):
        if kind == 'disarmed':
            r.setdefault('stop_reason', d.get('reason'))
            if '程序退出' in str(d.get('reason') or ''): r['flush_now'] = True
        self._common(r, kind, d, wall)

    def _common(self, r, kind, d, wall):
        if kind == 'process_termination_request':
            r['exit_request'] = d.get('request_wall_ns') or wall
            r['exit_accepted'] = d.get('accepted'); r['exit_call_ms'] = d.get('call_ms')
        elif kind == 'death_exit_timing':
            r['suspicious_to_exit_ms'] = d.get('suspicious_to_exit_ms')
            if r.get('suspicious') is None: r['suspicious'] = self._perf(d.get('first_full_monotonic'))
        elif kind == 'game_window_gone': r.setdefault('window_gone', wall)
        elif kind == 'exit_observation':
            r['exit_check'] = d.get('state')
            if d.get('state') in _EXIT_STATES: r['exit_final'] = wall
        elif kind == 'settlement_log_seen':
            r['end_requests'] += 1
            if r.get('end_request_log') is None:
                r['end_request_log'] = _game_log_ns(d.get('source_timestamp'))
                r['end_request_seen'] = d.get('seen_wall_ns') or wall
                r['end_request_id'] = d.get('request_id')
        elif kind == 'settlement_watch_started' and d.get('folder'):
            r['log_paths'].update(str(Path(d['folder']) / name) for name in d.get('files') or [])
        elif kind == 'game_end_request_summary':
            r['log_paths'].update(str(p) for p in d.get('log_paths') or [] if p)
            if d.get('found'): r['end_request_found'] = True
            if r.get('end_request_log') is None: r['end_request_log'] = _game_log_ns(d.get('source_timestamp'))
        elif kind.startswith('network_'):
            self._network(r, kind, d, wall)

    def _network(self, r, kind, d, wall):
        if (kind == 'network_state' and d.get('phase') not in (None, 'off')) or kind in (
                'network_manual_start', 'network_death', 'network_candidate_requested', 'network_prepare_ack'):
            r['network_active'] = True
        if kind == 'network_death':
            sig = d.get('signal') if isinstance(d.get('signal'), dict) else {}
            r['net_request'] = sig.get('signal_wall_ns') or wall
        elif kind == 'network_candidate_requested': r.setdefault('candidate_request', wall)
        elif kind == 'network_candidate_ack': r.setdefault('candidate_ack', wall)
        elif kind == 'network_candidate_released': r['candidate_released'] = wall
        elif kind == 'network_death_hold_ack':
            r['net_hold_ack'] = _iso_ns(d.get('broker_timestamp')) or wall; r['net_filters'] = d.get('filter_count')
        elif kind == 'network_restore_ack': r['net_restored'] = _iso_ns(d.get('broker_timestamp')) or wall
        elif kind in ('network_death_cutoff_error', 'network_error'): r['net_error'] = d.get('reason') or kind
        rec = d.get('record') if isinstance(d.get('record'), dict) else None
        if rec is None: return
        at = _iso_ns(rec.get('timestamp')); event = rec.get('event')
        if event == 'death_cutoff_received': r.setdefault('net_received', at)
        elif event == 'filter_transaction_committed':
            if len(r['commits']) < 64: r['commits'].append((_iso_ns(rec.get('commit_finished_timestamp')) or at, rec.get('commit_ms')))
        elif event == 'classify_drop':
            if len(r['drops']) < 4096: r['drops'].append((at, None))

    def _ready(self, r, wall):
        if r.get('end') is None: return None
        if r.get('flush_now'): return self._take(r)
        death_exit = r.get('exit_request') is not None
        exit_done = (not death_exit) or r.get('exit_final') is not None
        net_done = (not death_exit) or (not r['network_active']) or r.get('net_restored') is not None
        if exit_done and net_done and r['done_at'] is None: r['done_at'] = wall
        limit = r['end'] + int(WAIT_AFTER_END * 1e9)
        r['deadline'] = limit if r['done_at'] is None else min(limit, r['done_at'] + int(SETTLE * 1e9))
        return self._take(r) if wall >= r['deadline'] else None

    def due(self, now_ns=None):
        """Timer callback: the finished summary whose wait has run out, else None."""
        now_ns = now_ns if now_ns is not None else time.time_ns()
        with self.lock:
            r = self.round
            if r is None or r.get('end') is None or r.get('deadline') is None or now_ns < r['deadline']: return None
            if self._scan(r, now_ns) == 'waiting_response' and r['scanned'] == 1:
                r['deadline'] = now_ns + int(RESPONSE_WAIT * 1e9); return None
            return self._take(r)

    def _scan(self, r, now_ns):
        """Fill end-request and server-response times from the game log itself.

        Catches a request written after the live watcher stopped (2026-11-19 15:47:58.519)."""
        r['scanned'] += 1
        if not r['log_paths']: return None
        end = r.get('end') or now_ns
        found = scan_end_requests(sorted(r['log_paths']), r['armed'] - int(5e9), min(now_ns, end + int(REQUEST_AFTER_END * 1e9 + RESPONSE_LIMIT * 1e9)))
        # A request belongs to this round only if it was sent by shortly after the round ended.
        found = {k: v for k, v in found.items() if v.get('request') is None or v['request'] <= end + int(REQUEST_AFTER_END * 1e9)}
        found = {k: v for k, v in found.items() if v.get('request') is not None or v.get('response', 0) <= end + int(RESPONSE_LIMIT * 1e9)}
        if not found: return None
        rid, item = min(found.items(), key=lambda kv: kv[1].get('request') or kv[1].get('response'))
        if item.get('request') is not None:
            if r.get('end_request_log') is None or item['request'] < r['end_request_log']:
                r['end_request_log'] = item['request']; r['end_request_id'] = rid
            r['end_request_found'] = True
        r['end_response_log'] = item.get('response'); r['end_response_code'] = item.get('code')
        r['end_requests'] = max(r['end_requests'], len(found))
        return 'waiting_response' if item.get('request') is not None and item.get('response') is None else 'complete'

    def pending_deadline(self):
        with self.lock:
            r = self.round
            return None if r is None or r.get('end') is None else r.get('deadline')

    def _take(self, r):
        if r is self.round: self.round = None
        if r['scanned'] == 0 or r.get('end_response_log') is None: self._scan(r, time.time_ns())
        return self._finish(r)

    def _finish(self, r):
        decision = r.get('decision')
        rel = lambda ns: None if ns is None or decision is None else round((ns - decision) / 1e6, 1)
        net_start = r.get('net_received') or r.get('net_request')
        commit = _first_at_or_after(r['commits'], net_start)
        drop = _first_at_or_after(r['drops'], net_start)
        c_commit = _first_at_or_after(r['commits'], r.get('candidate_request'))
        c_drop = _first_at_or_after(r['drops'], r.get('candidate_request'))
        if net_start is not None:   # candidate phase only counts what happened before the death cutoff
            c_commit = c_commit if c_commit and c_commit[0] < net_start else None
            c_drop = c_drop if c_drop and c_drop[0] < net_start else None
        r['net_committed'] = commit[0] if commit else None; r['net_commit_ms'] = commit[1] if commit else None
        r['net_first_drop'] = drop[0] if drop else None
        r['candidate_committed'] = c_commit[0] if c_commit else None
        r['candidate_first_drop'] = c_drop[0] if c_drop else None
        r['net_drops'] = sum(1 for t, _ in r['drops'] if net_start is not None and t is not None and t >= net_start)
        s = dict(
            outcome=r.get('outcome', '未知'), started_by=r['how'], after_game_restart=r['after_restart'],
            armed_time=_local(r['armed']), end_time=_local(r.get('end')),
            duration_s=None if r.get('end') is None else round((r['end'] - r['armed']) / 1e9, 1),
            dry_run=r.get('dry_run'), stop_reason=r.get('stop_reason'),
            death_basis=r.get('death_basis'), extraction_basis=r.get('extraction_basis'),
            ui_last_seen_vs_zero_ms=r.get('ui_vs_zero_ms'), zone_remaining_s=r.get('zone_remaining_s'),
            decision_time=_local(decision), suspicious_time=_local(r.get('suspicious')),
            suspicious_to_decision_ms=None if r.get('suspicious') is None or decision is None else round((decision - r['suspicious']) / 1e6, 1),
            exit_request_time=_local(r.get('exit_request')), exit_request_after_decision_ms=rel(r.get('exit_request')),
            exit_accepted=r.get('exit_accepted'), exit_call_ms=r.get('exit_call_ms'),
            suspicious_to_exit_request_ms=r.get('suspicious_to_exit_ms'),
            window_gone_time=_local(r.get('window_gone')), window_gone_after_decision_ms=rel(r.get('window_gone')),
            process_exit_state=r.get('exit_check'), process_exit_time=_local(r.get('exit_final')),
            process_exit_after_decision_ms=rel(r.get('exit_final')),
            network_active=r['network_active'],
            network_request_time=_local(r.get('net_request')), network_request_after_decision_ms=rel(r.get('net_request')),
            network_received_time=_local(r.get('net_received')), network_received_after_decision_ms=rel(r.get('net_received')),
            network_rules_committed_time=_local(r['net_committed']), network_rules_committed_after_decision_ms=rel(r['net_committed']),
            network_commit_ms=r['net_commit_ms'], network_filters=r.get('net_filters'),
            network_first_block_time=_local(r['net_first_drop']), network_first_block_after_decision_ms=rel(r['net_first_drop']),
            network_block_events=r['net_drops'],
            network_hold_ack_time=_local(r.get('net_hold_ack')), network_hold_ack_after_decision_ms=rel(r.get('net_hold_ack')),
            network_restored_time=_local(r.get('net_restored')), network_error=r.get('net_error'),
            candidate_isolation_request_time=_local(r.get('candidate_request')),
            candidate_isolation_committed_time=_local(r['candidate_committed']),
            candidate_isolation_first_block_time=_local(r['candidate_first_drop']),
            candidate_isolation_released_time=_local(r.get('candidate_released')),
            end_request_logged=bool(r.get('end_request_log') or r.get('end_request_found')),
            end_request_count=r['end_requests'], end_request_id=r.get('end_request_id'),
            end_request_game_log_time=_local(r.get('end_request_log')), end_request_read_time=_local(r.get('end_request_seen')),
            end_request_vs_decision_ms=rel(r.get('end_request_log')),
            end_response_game_log_time=_local(r.get('end_response_log')),
            end_response_vs_decision_ms=rel(r.get('end_response_log')), end_response_code=r.get('end_response_code'),
            transfers=r['transfers'], zone_pauses=r['zone_pauses'])
        effective = r['net_first_drop'] or r['net_committed']
        if r.get('end_request_log') is not None and effective is not None:
            s['end_request_vs_network_effective_ms'] = round((r['end_request_log'] - effective) / 1e6, 1)
        if r.get('end_request_log') is not None and r.get('exit_request') is not None:
            s['end_request_vs_exit_request_ms'] = round((r['end_request_log'] - r['exit_request']) / 1e6, 1)
        if r.get('end_response_log') is not None and effective is not None:
            s['end_response_vs_network_effective_ms'] = round((r['end_response_log'] - effective) / 1e6, 1)
        if r.get('end_response_log') is not None and r.get('exit_request') is not None:
            s['end_response_vs_exit_request_ms'] = round((r['end_response_log'] - r['exit_request']) / 1e6, 1)
        s['text'] = render(s, r)
        return s


def _ms(v):
    return '—' if v is None else ('+' if v >= 0 else '') + f'{v:.0f} ms'


def render(s, r):
    lines = ['结果：' + s['outcome'] + ('（演练）' if s.get('dry_run') else '')]
    start = {'restart': '（游戏重启后自动开启）', 'resumed': '（自动暂停后识别到新一局）'}.get(s['started_by'], '')
    lines.append(f"监控：{_clock(r['armed'])} 开启{start} → {_clock(r.get('end'))} 结束，共 {s['duration_s']} 秒"
                 + (f"；停止原因：{s['stop_reason']}" if s.get('stop_reason') else ''))
    if s['death_basis']: lines.append('死亡依据：' + s['death_basis'])
    if s['extraction_basis']:
        extra = '' if s.get('ui_last_seen_vs_zero_ms') is None else f"（HUD最后出现相对归零 {_ms(s['ui_last_seen_vs_zero_ms'])}）"
        lines.append('撤离依据：' + str(s['extraction_basis']) + extra)
    if s.get('zone_remaining_s') is not None:
        lines.append(f"自动暂停结束时倒计时剩余：{s['zone_remaining_s']:.1f} 秒")
    if r.get('decision') is not None:
        lines.append(f"判定时刻：{_clock(r['decision'])}（以下括号内为相对判定时刻）")
    if r.get('suspicious') is not None:
        lines.append(f"首次可疑画面：{_clock(r['suspicious'])}，至判定 {s['suspicious_to_decision_ms']} ms")
    if r.get('exit_request') is not None:
        lines.append(f"大退请求：{_clock(r['exit_request'])}（{_ms(s['exit_request_after_decision_ms'])}），"
                     f"{'已受理' if s['exit_accepted'] else '未受理'}，接口 {s['exit_call_ms']} ms；"
                     f"可疑画面至大退请求 {s['suspicious_to_exit_request_ms']} ms")
        lines.append(f"游戏窗口消失：{_clock(r.get('window_gone'))}（{_ms(s['window_gone_after_decision_ms'])}）；"
                     f"进程退出：{_EXIT_STATES.get(s['process_exit_state'], s['process_exit_state'] or '未确认')} "
                     f"{_clock(r.get('exit_final'))}（{_ms(s['process_exit_after_decision_ms'])}）")
    if not s['network_active']:
        lines.append('断网保险：本局未启用')
    else:
        if r.get('candidate_request') is not None:
            lines.append(f"提前临时隔离：请求 {_clock(r['candidate_request'])}，规则部署 {_clock(r['candidate_committed'])}，"
                         f"首次拦截 {_clock(r['candidate_first_drop'])}，解除 {_clock(r.get('candidate_released'))}")
        if r.get('net_request') is not None:
            lines.append(f"死亡断网请求：{_clock(r['net_request'])}（{_ms(s['network_request_after_decision_ms'])}）；"
                         f"联网助手收到：{_clock(r.get('net_received'))}（{_ms(s['network_received_after_decision_ms'])}）")
            lines.append(f"断网规则部署完成：{_clock(r['net_committed'])}（{_ms(s['network_rules_committed_after_decision_ms'])}，"
                         f"提交耗时 {s['network_commit_ms']} ms，规则 {s['network_filters']} 条）")
            lines.append(f"断网生效（首次拦截到游戏连接）：{_clock(r['net_first_drop']) if r['net_first_drop'] else '未观察到'}"
                         f"（{_ms(s['network_first_block_after_decision_ms'])}，拦截事件 {s['network_block_events']} 次）")
        else:
            lines.append('死亡断网：本局未执行')
        if r.get('net_restored') is not None: lines.append('恢复联网：' + _clock(r['net_restored']))
        if s.get('network_error'): lines.append('断网异常：' + str(s['network_error']))
    if s['end_request_logged']:
        lines.append(f"游戏结束请求日志：已记录（编号 {s['end_request_id'] or '—'}，共 {s['end_request_count']} 次），"
                     f"游戏日志时刻 {_clock(r.get('end_request_log'))}（{_ms(s['end_request_vs_decision_ms'])}），"
                     + (f"助手实时读到 {_clock(r['end_request_seen'])}" if r.get('end_request_seen') else '监控结束后从游戏日志补查到'))
        if r.get('end_response_log') is not None:
            code = '' if s.get('end_response_code') is None else f"，状态码 {s['end_response_code']}"
            lines.append(f"服务器响应结束请求：{_clock(r['end_response_log'])}（{_ms(s['end_response_vs_decision_ms'])}{code}）")
        else:
            lines.append('服务器响应结束请求：游戏日志里未记录到')
        for prefix, keys in (('结束请求', ('end_request_vs_network_effective_ms', 'end_request_vs_exit_request_ms')),
                             ('服务器响应', ('end_response_vs_network_effective_ms', 'end_response_vs_exit_request_ms'))):
            cmp = []
            for key, label in zip(keys, ('断网生效', '大退请求')):
                v = s.get(key)
                if v is not None: cmp.append(f"比{label}{'早' if v < 0 else '晚'} {abs(v):.0f} ms")
            if cmp: lines.append(prefix + '，'.join(cmp) + '（按游戏日志时刻）')
        v = s.get('end_response_vs_network_effective_ms')
        if v is not None and v < 0:
            lines.append('注意：断网生效前游戏已收到服务器对结束请求的响应，本局结果可能已经保存')
    else:
        lines.append('游戏结束请求日志：未记录到（不等于一定未发送）')
    other = []
    if s['transfers']: other.append(f"转移 {s['transfers']} 次")
    if s['zone_pauses']: other.append(f"撤离/转移区自动暂停 {s['zone_pauses']} 次")
    if other: lines.append('本局其他：' + '，'.join(other))
    return lines
