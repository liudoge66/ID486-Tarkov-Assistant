"""Per-raid summary ('本局结束汇总'): one block per round in 运行记录.txt and one record in monitor_events.jsonl."""
import io,json,os,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import round_summary as RS
from round_summary import RoundSummary
from event_log import EventJournal

S=1_000_000_000
T0=1_795_076_000*S


def iso(ns):
    from datetime import datetime
    return datetime.fromtimestamp(ns/1e9).astimezone().isoformat(timespec='microseconds')


def game(ns):
    from datetime import datetime
    return datetime.fromtimestamp(ns/1e9).strftime('%Y-%m-%d %H:%M:%S.%f')[:23]


def death_round(folder=None):
    """A death exit with the network cutoff, like 2026-11-19 16:27 (times in seconds from T0)."""
    t=lambda s:T0+int(s*S)
    ev=[(0,'armed',dict(dry_run=False)),
        (1,'network_state',dict(phase='armed')),
        (2,'settlement_watch_started',dict(folder=str(folder) if folder else None,files=['backend_000.log'])),
        (99.437,'settlement_log_seen',dict(request_id='427',source_timestamp=game(t(99.437)),seen_wall_ns=t(99.441))),
        (100.889,'death_decision',dict(decision_wall_ns=t(100.889),diagnostic_episode={})),
        (100.889,'process_termination_request',dict(request_wall_ns=t(100.889),accepted=True,call_ms=.8)),
        (100.889,'death_exit_timing',dict(suspicious_to_exit_ms=1468.)),
        (100.889,'network_death',dict(signal=dict(signal_wall_ns=t(100.889)))),
        (100.890,'trigger',dict(dry_run=False)),
        (100.947,'game_window_gone',{}),
        (100.95,'network_broker_audit',dict(record=dict(event='death_cutoff_received',timestamp=iso(t(100.890))))),
        (100.95,'network_rules',dict(record=dict(event='filter_transaction_committed',timestamp=iso(t(100.924)),commit_finished_timestamp=iso(t(100.924)),commit_ms=10.1))),
        (100.95,'network_block_hit',dict(record=dict(event='classify_drop',timestamp=iso(t(100.919))))),
        (100.951,'network_death_hold_ack',dict(broker_timestamp=iso(t(100.932)),filter_count=36)),
        (100.98,'game_end_request_summary',dict(found=True,source_timestamp=game(t(99.437)))),
        (104.198,'exit_observation',dict(state='exited')),
        (104.25,'network_restore_ack',dict(broker_timestamp=iso(t(104.203))))]
    return [(t(a),k,d) for a,k,d in ev]


class RoundSummaryTests(unittest.TestCase):
    def run_events(self,events,extra_after=20):
        rs=RoundSummary(T0,0);out=[]
        for wall,kind,d in events:out+=rs.observe(kind,d,wall)
        due=rs.due(events[-1][0]+int(extra_after*S))
        return out+([due] if due else [])

    def test_death_summary_has_every_timing(self):
        [s]=self.run_events(death_round())
        self.assertEqual(s['outcome'],'死亡')
        self.assertEqual(s['exit_request_after_decision_ms'],0.)
        self.assertEqual(s['window_gone_after_decision_ms'],58.)
        self.assertEqual(s['process_exit_after_decision_ms'],3309.)
        self.assertEqual(s['network_received_after_decision_ms'],1.)
        self.assertEqual(s['network_rules_committed_after_decision_ms'],35.)
        self.assertEqual(s['network_first_block_after_decision_ms'],30.)
        self.assertEqual(s['network_commit_ms'],10.1);self.assertEqual(s['network_filters'],36)
        self.assertTrue(s['end_request_logged']);self.assertEqual(s['end_request_vs_decision_ms'],-1452.)
        self.assertEqual(s['end_request_vs_network_effective_ms'],-1482.)
        text='\n'.join(s['text'])
        for part in ('结果：死亡','大退请求','断网规则部署完成','断网生效','游戏结束请求日志：已记录','比断网生效早 1482 ms'):
            self.assertIn(part,text)

    def test_waits_for_exit_and_restore_before_writing(self):
        rs=RoundSummary(T0,0);events=death_round();written=[]
        for wall,kind,d in events[:-2]:written+=rs.observe(kind,d,wall)
        self.assertEqual(written,[])                         # process exit / restore not seen yet
        self.assertIsNone(rs.due(events[-3][0]+int(1*S)))
        for wall,kind,d in events[-2:]:written+=rs.observe(kind,d,wall)
        self.assertEqual(written,[])                         # settle window for late audit records
        self.assertIsNotNone(rs.due(events[-1][0]+int((RS.SETTLE+.1)*S)))

    def test_server_response_found_in_game_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=lambda s:T0+int(s*S)
            Path(tmp,'backend_000.log').write_text(
                f"{game(t(99.437))}|1.3|Info|backend|---> Request HTTPS, id [427]: URL: https://gw-pve.escapefromtarkov.com/client/match/local/end. \n"
                f"{game(t(100.674))}|1.3|Info|backend|<--- Response HTTPS, id [427]: URL: https://gw-pve.escapefromtarkov.com/client/match/local/end, DownloadSeconds: 1.2, responseText:{{\"secret\":1}}\n",
                encoding='utf-8')
            [s]=self.run_events(death_round(tmp))
        self.assertEqual(s['end_response_vs_decision_ms'],-215.)
        self.assertLess(s['end_response_vs_network_effective_ms'],0)
        self.assertIn('本局结果可能已经保存','\n'.join(s['text']))
        self.assertNotIn('secret',json.dumps(s,ensure_ascii=False))

    def test_late_end_request_is_recovered_and_next_raid_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=lambda s:T0+int(s*S)
            Path(tmp,'backend_000.log').write_text(
                f"{game(t(10.2))}|1.3|Info|backend|---> Request HTTPS, id [156]: URL: https://h.escapefromtarkov.com/client/match/local/end. \n"
                f"{game(t(11.4))}|1.3|Info|backend|<--- Response HTTPS, id [156]: URL: https://h.escapefromtarkov.com/client/match/local/end, x\n"
                f"{game(t(500))}|1.3|Info|backend|---> Request HTTPS, id [427]: URL: https://h.escapefromtarkov.com/client/match/local/end. \n",
                encoding='utf-8')
            ev=[(t(0),'armed',{}),(t(1),'settlement_watch_started',dict(folder=tmp,files=['backend_000.log'])),
                (t(10),'extraction_transition',dict(evidence=dict(context='combat'))),
                (t(10.004),'disarmed',dict(reason='识别到撤离渐黑过渡，停止监控（未执行退出）'))]
            [s]=self.run_events(ev,extra_after=600)
        self.assertEqual(s['outcome'],'撤离')
        self.assertEqual(s['end_request_id'],'156');self.assertEqual(s['end_request_count'],1)
        self.assertIn('监控结束后从游戏日志补查到','\n'.join(s['text']))

    def test_manual_stop_without_network(self):
        [s]=self.run_events([(T0,'armed',{}),(T0+S,'disarmed',dict(reason='F10 手动关闭'))])
        self.assertEqual(s['outcome'],'手动停止');self.assertFalse(s['network_active'])
        self.assertIn('断网保险：本局未启用','\n'.join(s['text']))

    def test_game_closed_while_paused_then_new_round_after_restart(self):
        rs=RoundSummary(T0,0);out=[]
        out+=rs.observe('armed',{},T0)
        out+=rs.observe('zone_game_exited',{},T0+60*S)
        out+=[rs.due(T0+62*S)]
        out+=rs.observe('armed',{},T0+120*S)
        out+=rs.observe('disarmed',dict(reason='F10 手动关闭'),T0+200*S)
        out+=[rs.due(T0+210*S)]
        self.assertEqual([s['outcome'] for s in out if s],['游戏已关闭（暂停判断期间）','手动停止'])
        self.assertTrue(out[-1]['after_game_restart'])

    def test_program_exit_writes_immediately(self):
        rs=RoundSummary(T0,0)
        rs.observe('armed',{},T0)
        out=rs.observe('disarmed',dict(reason='程序退出'),T0+S)
        self.assertEqual(len(out),1);self.assertEqual(out[0]['outcome'],'程序退出')


class JournalOutputTests(unittest.TestCase):
    def test_summary_written_to_both_logs(self):
        text=io.StringIO();jsonl=io.StringIO()
        j=EventJournal(text,False,jsonl_stream=jsonl)
        j.write('armed',dict(dry_run=False))
        j.write('disarmed',dict(reason='程序退出'))
        self.assertIn('本局结束汇总',text.getvalue());self.assertIn('    结果：程序退出',text.getvalue())
        records=[json.loads(l) for l in jsonl.getvalue().splitlines()]
        summary=[r for r in records if r['event']=='round_summary']
        self.assertEqual(len(summary),1);self.assertEqual(summary[0]['details']['outcome'],'程序退出')

    def test_timer_writes_summary_after_wait(self):
        text=io.StringIO()
        j=EventJournal(text,False,jsonl_stream=io.StringIO())
        j.write('armed',{});j.write('disarmed',dict(reason='F10 手动关闭'))
        self.assertNotIn('本局结束汇总',text.getvalue())
        time.sleep(RS.SETTLE+.5)
        self.assertIn('本局结束汇总',text.getvalue())

    def test_summary_failure_never_breaks_logging(self):
        text=io.StringIO()
        j=EventJournal(text,False,jsonl_stream=io.StringIO())
        j.rounds.observe=lambda *a:1/0
        j.write('armed',{})
        self.assertIn('死亡监控已开启',text.getvalue());self.assertIn('本局汇总生成失败',text.getvalue())


if __name__=='__main__':unittest.main()
