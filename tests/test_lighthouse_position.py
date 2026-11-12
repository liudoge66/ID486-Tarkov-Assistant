"""Synthetic coordinate/map/control tests. No real game, key input or WFP."""
import copy,json,os,sys,tempfile,threading,types,unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'src' if (ROOT/'src').is_dir() else ROOT/'src'
sys.path.insert(0,str(SOURCE))
from lighthouse_position import parse_position,map_event,bind_session,MapLog,PositionPolicy,LocationMonitor,ScreenshotRetention
import game_keys
from detector import Gate
from decision import DecisionEngine

ZONE=json.loads((SOURCE/'assets/lighthouse_zone.json').read_text(encoding='utf-8'))

class Clock:
    def __init__(self):self.now=10.;self.wall=datetime(2026,11,4,20,0,0).timestamp()
    def move(self,delta):self.now+=delta;self.wall+=delta


class PositionTests(unittest.TestCase):
    def test_raid_transit_summary_preserves_detected_map(self):
        prefix='2026-11-09 12:55:48.849|1.3.0.1.48558|Info|application|'
        for summary in ('Flag:None, RaidId:test, Count:0, Locations:Lighthouse -> ',
                        'Flag:Common, RaidId:test, Count:1, Locations:Woods -> Lighthouse -> '):
            self.assertIsNone(map_event(prefix+'[Transit] '+summary))
        self.assertEqual(map_event(prefix+'GameStopped:'),('reset',None))
    def test_filename_and_bind(self):
        name='2026-09-07[23-05]_58.02, 1.75, 49.47_0.01518, 0.90924, -0.03197, 0.41476_15.47 (0).png'
        self.assertEqual(parse_position(name),(58.02,1.75,49.47))
        for bad in ('codex-clipboard-123.png',name.replace('58.02','nan'),name.replace('.png','.jpg'),name.replace('58.02','1000001')):
            self.assertIsNone(parse_position(bad))

    def test_screenshot_binding_follows_game_and_reports_conflicts(self):
        class Env:
            free=True
            def probe(self,combo):return self.free
            def steam_shortcuts(self):return [((0,123),'Steam 截图键'),((4,9),'Steam 界面快捷键')]
        env=Env();tool=(0,0x79)
        def ini(path,screenshot):
            binds=[dict(keyName='MakeScreenshot',variants=[dict(keyCode=v) for v in screenshot]+[dict(keyCode=[])]),
                   dict(keyName='ReloadWeapon',variants=[dict(keyCode=['R'])]),dict(keyName='Sprint',variants=[dict(keyCode=['LeftShift'])]),
                   dict(keyName='ChamberUnload',variants=[dict(keyCode=['R','LeftControl'])]),
                   dict(keyName='ScopeZoomIn',variants=[dict(isAxis=True,keyCode=['LeftAlt'],axisName='Mouse Scroll')])]
            path.write_text(json.dumps(dict(keyBindings=binds,axisBindings=[dict(axisName='MoveY',pairs=[dict(positive=dict(keyCode=['W']))])])),encoding='utf-8')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'Control.ini'
            ini(p,[['F12'],['KeypadMultiply']])
            r=game_keys.analyze({},p,tool,env)
            self.assertTrue(r['usable']);self.assertEqual(r['keys'],((0x6a,0x37,0),));self.assertEqual(r['hint'],'')
            self.assertIn('Steam 截图键',r['checked'][0]['problems'][0])
            ini(p,[['F12']]);r=game_keys.analyze({},p,tool,env)
            self.assertFalse(r['usable']);self.assertIn('F12',r['hint']);self.assertIn('更换',r['hint'])
            ini(p,[]);r=game_keys.analyze({},p,tool,env);self.assertFalse(r['usable']);self.assertIn('绑定',r['hint'])
            cases=[('R','ReloadWeapon'),(['R','LeftControl'],'ChamberUnload'),('W','MoveY'),('SysReq','Windows 截图'),
                   ('F10','助手开关'),('Mouse3','不能发送'),('Escape','不能发送'),(['T','B'],'单键'),('Banana','无法识别'),
                   (['Tab','LeftShift'],'Steam 界面'),(['F4','LeftAlt'],'Alt+F4')]
            for key,reason in cases:
                r=game_keys.analyze(dict(lighthouse_screenshot_key=key),p,tool,env)
                self.assertFalse(r['usable'],key);self.assertIn(reason,r['hint'],key)
            r=game_keys.analyze(dict(lighthouse_screenshot_key=['KeypadMultiply','LeftShift']),p,tool,env)
            self.assertTrue(r['usable']);self.assertEqual(r['keys'],((0xA0,0x2A,0),(0x6a,0x37,0)));self.assertIn('Sprint',r['hint'])
            r=game_keys.analyze(dict(lighthouse_screenshot_key=['KeypadMultiply','LeftAlt']),p,tool,env)
            self.assertIn('ScopeZoomIn',r['hint'])
            env.free=False;r=game_keys.analyze(dict(lighthouse_screenshot_key='F9'),p,tool,env)
            self.assertFalse(r['usable']);self.assertIn('其他程序',r['hint'])
            r=game_keys.analyze({},Path(tmp)/'missing.ini',tool,env);self.assertFalse(r['usable']);self.assertIn('未找到',r['hint'])
        self.assertEqual(game_keys.steam_combo('Shift\tKEY_TAB'),(4,9));self.assertEqual(game_keys.steam_combo('KEY_F12'),(0,123))
        self.assertIsNone(game_keys.steam_combo(''))

    def test_intervals_height_and_hysteresis(self):
        p=PositionPolicy(ZONE);p.reset_map('Lighthouse');x,y,z=ZONE['center']
        # (distance-32)/6-1.5, clamped to 3..60
        for i,(distance,expected) in enumerate(((600,60),(300,43.2),(200,26.5),(100,9.8),(50,3),(33,3))):
            p.observe((x+distance,y,z),i);self.assertEqual(p.interval,expected)
        p.observe((x,y,z),9.);self.assertTrue(p.protected);self.assertEqual(p.interval,6.)  # (45-0)/6-1.5
        p.observe((x+31,y,z),10.);self.assertTrue(p.protected);self.assertEqual(p.interval,3.)
        p.observe((x+40,y,z),11.);self.assertTrue(p.protected)
        p.observe((x+46,y,z),12.);self.assertFalse(p.protected)
        p.observe((x,0,z),13.);self.assertFalse(p.protected)
        p.observe((x,y,z),14.);self.assertTrue(p.protected)
        self.assertTrue(p.snapshot(100.)['stale']);self.assertTrue(p.protected)
        p.reset_map('woods');self.assertFalse(p.protected);self.assertIsNone(p.position)

    def test_session_and_complete_log_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);started=datetime(2026,11,4,8,0,0).timestamp()
            correct=root/'log_2026.11.04_8-00-00_1.3.0';correct.mkdir()
            (root/'log_2026.11.03_8-00-00_1.3.0').mkdir()
            self.assertEqual(bind_session(root,started,started+3600),correct)
            p=correct/'2026 application_000.log'
            line=b'2026-11-04 08:00:00.000|v|Info|application|scene preset path:maps/lighthouse_preset.bundle\n'
            p.write_bytes(line[:-1]);reader=MapLog(correct)
            self.assertEqual(reader.poll(),[])
            with p.open('ab') as f:f.write(b'\n')
            self.assertEqual(reader.poll(),[('map','Lighthouse')]);self.assertEqual(reader.poll(),[])
            p.write_bytes(b'v|application|Matching with group id: 1\n')
            self.assertIn(('uncertain',None),reader.poll())
            rotated=correct/'2026 application_001.log';rotated.write_bytes(b'v|application|scene preset path:maps/woods_preset.bundle\n')
            self.assertIn(('map','woods'),reader.poll())

    def fixture(self,tmp,map_name='lighthouse',retention=None):
        clock=Clock();folder=Path(tmp)/'logs';folder.mkdir();shots=Path(tmp)/'shots';shots.mkdir()
        log=folder/'application_000.log';log.write_text('v|application|scene preset path:maps/'+map_name+'_preset.bundle\n')
        owner=dict(alive=True,foreground=True);presses=[];events=[];states=[]
        monitor=LocationMonitor(folder,ZONE,[shots],lambda:presses.append(clock.now) or True,
            lambda:owner['alive'],lambda:owner['foreground'],lambda k,**d:events.append((k,d)),states.append,
            clock=lambda:clock.now,wall=lambda:clock.wall,retention=retention(shots,clock) if retention else None)
        monitor.tick();monitor.confirm_hud(clock.now)
        return monitor,clock,shots,owner,presses,events,log

    def shot(self,shots,clock,position,number=0,old_name=False):
        stamp=datetime.fromtimestamp(clock.wall-600 if old_name else clock.wall).strftime('%Y-%m-%d[%H-%M]')
        p=shots/f'{stamp}_{position[0]:.2f}, {position[1]:.2f}, {position[2]:.2f}_0.0, 0.0, 0.0, 1.0_1.00 ({number}).png'
        p.write_bytes(b'filename-only-test');os.utime(p,(clock.wall,clock.wall));return p

    def test_standby_discards_pending_without_retry_or_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.tick();self.assertEqual(len(presses),1)
            self.assertIsNotNone(m.pending)
            m.set_paused(True)
            self.assertIsNone(m.pending)
            for _ in range(10):c.move(60);m.tick()
            self.assertEqual(len(presses),1);self.assertEqual(m.failures,0)
            self.assertFalse(any(k=='lighthouse_screenshot_missing' for k,_ in events))

    def test_lighthouse_map_without_hud_never_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.hud_epoch=None
            c.move(60);m.tick();self.assertEqual(presses,[])
            m.confirm_hud(c.now);m.tick();self.assertEqual(len(presses),1)

    def test_resume_alone_does_not_enable_screenshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.set_paused(True);c.move(10);m.set_paused(False)
            m.confirm_hud(c.now-1);m.tick();self.assertEqual(presses,[])
            m.confirm_hud(c.now);m.tick();self.assertEqual(len(presses),1)

    def test_other_map_hud_does_not_start_and_new_lighthouse_needs_fresh_hud(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp,map_name='woods')
            m.confirm_hud(c.now);m.tick();self.assertEqual(presses,[])
            old=c.now
            with log.open('a') as f:f.write('v|application|scene preset path:maps/lighthouse_preset.bundle\n')
            c.move(2);m.tick();m.confirm_hud(old);m.tick();self.assertEqual(presses,[])
            m.confirm_hud(c.now);m.tick();self.assertEqual(len(presses),1)

    def test_started_in_standby_does_not_screenshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.set_paused(True);m.tick();c.move(60);m.tick()
            self.assertEqual(presses,[]);self.assertEqual(m.policy.map,'Lighthouse')

    def test_resume_uses_fresh_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.tick();m.set_paused(True)
            c.move(1);self.shot(shots,c,ZONE['center']);m.tick()
            self.assertIsNone(m.policy.position)
            m.set_paused(False);m.confirm_hud(c.now);m.tick()
            self.assertEqual(len(presses),2);self.assertIsNone(m.policy.position)
            c.move(1);self.shot(shots,c,ZONE['center'],number=1);m.tick()
            self.assertTrue(m.policy.protected)

    def test_map_updates_while_standby_and_other_map_never_screenshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.set_paused(True);m.tick()
            with log.open('a') as f:f.write('v|application|scene preset path:maps/woods_preset.bundle\n')
            c.move(2);m.tick();self.assertEqual(m.policy.map,'woods')
            m.set_paused(False);m.tick();self.assertEqual(presses,[])

    def test_f10_session_schedule_and_fresh_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.tick();self.assertEqual(presses,[10.])
            c.move(1);x,y,z=ZONE['center'];self.shot(shots,c,(x+600,y,z));m.tick()
            self.assertFalse(m.snapshot()['protected']);self.assertEqual(m.next_shot,c.now+60)
            c.move(59);m.tick();self.assertEqual(len(presses),1)
            c.move(1);m.tick();self.assertEqual(len(presses),2)
            c.move(1);self.shot(shots,c,(x,y,z));m.tick()
            self.assertTrue(m.snapshot()['protected']);self.assertEqual(m.policy.interval,6)
            c.move(6);owner['foreground']=False;m.tick();self.assertEqual(len(presses),2)
            owner['foreground']=True;m.tick();self.assertEqual(len(presses),3)
            c.move(1);self.shot(shots,c,(x+50,y,z));m.tick();self.assertFalse(m.snapshot()['protected'])
            owner['alive']=False;c.move(40);m.tick();self.assertEqual(len(presses),3)

    def test_retention_keeps_newest_own_screenshots_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest=Path(tmp)/'data'/'owned.json'
            make=lambda shots,clock:ScreenshotRetention(manifest,[shots],limit=2,min_age=0,wall=lambda:clock.wall)
            m,c,shots,owner,presses,events,log=self.fixture(tmp,retention=make)
            outside=Path(tmp)/'elsewhere';outside.mkdir()
            stray=self.shot(outside,c,ZONE['center'],number=99)
            own=[]
            m.tick()
            for i in range(4):
                c.move(1);own.append(self.shot(shots,c,ZONE['center'],number=i));m.tick()
                c.move(6);m.tick()
            self.assertEqual(len(presses),5)
            owner['foreground']=False;c.move(20);manual=self.shot(shots,c,ZONE['center'],number=50);m.tick()
            self.assertEqual([p.exists() for p in own],[False,False,True,True])
            self.assertTrue(manual.exists())
            self.assertEqual(json.loads(manifest.read_text(encoding='utf-8'))['owned'],[str(p) for p in own[2:]])
            # A tampered manifest entry outside the screenshot folder is never deleted.
            manifest.write_text(json.dumps(dict(owned=[str(stray)]+[str(p) for p in own[2:]])),encoding='utf-8')
            r=ScreenshotRetention(manifest,[shots],limit=0,min_age=0,wall=lambda:c.wall);r.prune()
            self.assertTrue(stray.exists());self.assertFalse(own[2].exists());self.assertTrue(manual.exists())
            # Files younger than min_age wait for the next pass.
            young=self.shot(shots,c,ZONE['center'],number=60)
            r=ScreenshotRetention(manifest,[shots],limit=0,min_age=10,wall=lambda:c.wall);r.claim(young);r.prune()
            self.assertTrue(young.exists());c.move(11);r.prune();self.assertFalse(young.exists())

    def test_zone_edges_beep_once_and_update_text(self):
        import monitor as mod
        class Var:
            def __init__(self,v=''):self.v=v
            def get(self):return self.v
            def set(self,v):self.v=v
        app=mod.App.__new__(mod.App);app.token=1;beeps=[];events=[]
        app.status=Var();app.bottom_status=Var();app.state_head=Var();app.prep_feedback=Var()
        app.sounds=types.SimpleNamespace(play_cue=lambda name='enabled.wav':beeps.append(name))
        app.event=lambda k,**d:events.append(k)
        lh=dict(map='Lighthouse',stale=False,failures=0)
        steps=[(dict(lh,protected=False),0,'坐标'),(dict(lh,protected=True),1,'候选断网已暂停'),
               (dict(lh,protected=True,stale=True),1,'定位待恢复'),(dict(lh,protected=False),2,'已离开商人区域'),
               (dict(lh,protected=False),2,'已离开商人区域'),(dict(lh,protected=True),3,'候选断网已暂停')]
        for payload,count,text in steps:
            app.position_feedback(payload);self.assertEqual(len(beeps),count,payload);self.assertIn(text,app.bottom_status.get())
        self.assertEqual(set(beeps),{'enabled.wav'});self.assertEqual(events.count('lighthouse_zone_cue'),3)
        self.assertEqual(app.state_head.get(),'灯塔区域 · 不判定死亡');self.assertEqual(app.zone_view['kind'],'lighthouse')
        # A new round starting inside the zone does not beep on its first report.
        app.token=2;app.position_feedback(dict(lh,protected=True));self.assertEqual(len(beeps),3)
        app.position_feedback(dict(lh,protected=False,failures=3));self.assertEqual(len(beeps),4);self.assertIn('定位暂停',app.status.get())
        # Unavailable resets the edge memory.
        app.position_feedback(dict(unavailable=True,reason='x'));app.position_feedback(dict(lh,protected=True));self.assertEqual(len(beeps),4)
        # A playback failure never breaks the status update.
        def broken(name='enabled.wav'):raise RuntimeError('no device')
        app.sounds=types.SimpleNamespace(play_cue=broken);app.position_feedback(dict(lh,protected=False))
        self.assertEqual(app.status.get(),mod.App.ZONE_LEFT)

    def test_cue_is_fixed_builtin_and_respects_mute(self):
        from sound_library import SoundLibrary
        with tempfile.TemporaryDirectory() as tmp:
            lib=SoundLibrary(tmp,SOURCE/'assets');played=[]
            lib._play_path=lambda path,gain:played.append((Path(path).name,gain())) or True
            self.assertTrue(lib.play_cue());self.assertEqual(played,[('enabled.wav',1.)])
            lib.muted=True;self.assertFalse(lib.play_cue());self.assertEqual(len(played),1)

    def test_cadence_validation(self):
        for bad in (dict(max_speed=0),dict(min_interval=1),dict(min_interval=40,max_interval=30),dict(latency='x')):
            with self.assertRaises(ValueError):PositionPolicy(dict(ZONE,cadence=dict(ZONE['cadence'],**bad)))
        self.assertEqual(PositionPolicy(dict(ZONE,cadence=None)).cadence['max_interval'],60.)

    def test_other_map_does_not_press(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp,'woods')
            m.tick();c.move(60);m.tick();self.assertEqual(presses,[])

    def test_old_and_failed_screenshots_do_not_supply_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.tick();c.move(1);self.shot(shots,c,ZONE['center'],old_name=True);m.tick()
            self.assertIsNone(m.policy.position)
            for i in range(3):
                c.move(8);m.tick();c.move(3);m.tick()
            self.assertEqual(m.failures,3);self.assertEqual(len(presses),3)
            c.move(60);m.tick();self.assertEqual(len(presses),3)
            self.shot(shots,c,ZONE['center'],number=4);c.move(1);m.tick()
            self.assertEqual(m.failures,0);self.assertTrue(m.snapshot()['protected'])

    def test_map_change_clears_old_position_and_pending_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            m,c,shots,owner,presses,events,log=self.fixture(tmp)
            m.tick();c.move(1);self.shot(shots,c,ZONE['center']);m.tick();self.assertTrue(m.policy.protected)
            with log.open('a') as f:f.write('v|application|scene preset path:maps/woods_preset.bundle\n')
            c.move(1);m.tick();self.assertFalse(m.policy.protected);self.assertIsNone(m.pending)

    def test_protection_blocks_all_policy_lanes_and_resets_absence(self):
        g=Gate(delay_ms=300);g.max_observation_gap=.12;g.active=True;e=DecisionEngine(g)
        absent=dict(combat_score=0.,body_shape=0.,bar_frames=0.,label_shape=0.,relative_death=dict(certified=True))
        e.set_position_guard(True)
        for t in (1.,2.,30.,100.):
            r=e.step(t,(0,0),absent,0.,{},None,{},{});self.assertEqual(r['action'],'position_wait')
        self.assertTrue(e.settlement_context()['protected']);self.assertTrue(e.isolation_context()['transition_protected'])
        self.assertFalse(e.relative_context(100.,0)['allowed']);self.assertEqual(g.absence_observed,0.)
        e.set_position_guard(False);absent.pop('relative_death')
        for t in (101.,101.1,101.2):self.assertEqual(e.step(t,(0,0),absent,0.,{},None,{},{} )['action'],'monitor')
        self.assertEqual(e.step(101.4,(0,0),absent,0.,{},None,{},{} )['action'],'death')

    def test_app_final_parallel_and_candidate_guards_without_native_calls(self):
        import monitor as mod
        app=mod.App.__new__(mod.App);app.token=7;app.active=True;app.closed=False;app.lock=threading.RLock()
        app.position_session=types.SimpleNamespace(token=7,snapshot=lambda:dict(protected=True))
        calls=[];app.network=types.SimpleNamespace(candidate_update=lambda c,**k:calls.append(c),end_request=lambda *a,**k:calls.append('end'))
        self.assertFalse(app.parallel_death(7,None,None,None,None,None))
        app.position_candidate(7,dict(action='isolate'));app.position_end_request(7,{})
        self.assertEqual(calls,[dict(action='cancel',reason='lighthouse_position')])
        app.token=8;self.assertFalse(app.position_guard_active(7))

    def test_log_uncertainty_keeps_existing_protection_until_fresh_confirmation(self):
        p=PositionPolicy(ZONE);p.reset_map('Lighthouse');p.observe(tuple(ZONE['center']),1.)
        p.invalidate_map();self.assertTrue(p.protected);self.assertTrue(p.snapshot(2.)['stale'])
        self.assertIsNone(p.position);self.assertFalse(p.observe(tuple(ZONE['center']),3.))
        p.reset_map('Lighthouse');self.assertTrue(p.protected)
        x,y,z=ZONE['center'];p.observe((x+100,y,z),4.);self.assertFalse(p.protected)

    def test_injected_screenshot_sender_skips_modifiers_and_releases_after_exception(self):
        import monitor as mod
        import queue
        app=mod.App.__new__(mod.App);app.token=1;app.active=True;app.closed=False;app.lock=threading.RLock()
        app.network=None;app.hotkey_service=types.SimpleNamespace(combo=(0,121));app.ui=queue.Queue();app.event=lambda *a,**k:None
        info=(111,222,1920,1080);calls=[];keys_sent=[];state=dict(modifier=False,foreground=True,raise_down=False)
        binding=dict(usable=True,keys=((0x2c,0x37,1),),label='PrintScreen',warnings=[],hint='')
        class Native:
            def GetAsyncKeyState(self,key):return 0x8000 if state['modifier'] and key==0x11 else 0
            def SendInput(self,count,packet,size):
                flags=packet[0].ki.dwFlags;calls.append(flags);keys_sent.append((packet[0].ki.wVk,flags))
                if state['raise_down'] and not flags&2:raise OSError('synthetic input failure')
                return 1
        with tempfile.TemporaryDirectory() as tmp:
            app._loaded_preferences=dict(lighthouse_screenshot_key='PrintScreen',lighthouse_screenshot_directory=tmp)
            factory=lambda *a,**k:types.SimpleNamespace(press=a[3],start=lambda:None,policy=types.SimpleNamespace(cadence={}))
            with patch.object(mod,'analyze_screenshot_binding',lambda *a,**k:binding),patch.object(mod,'LocationMonitor',factory),patch.object(mod,'U',Native()),patch.object(mod,'target',lambda:info),patch.object(mod,'foreground_owner',lambda i:state['foreground']),patch.object(mod.time,'sleep',lambda t:None):
                position=app.start_position_session(1,info,None,Path(tmp))
                self.assertTrue(position.press());self.assertEqual(calls,[1,3]);calls.clear()
                state['modifier']=True;self.assertIsNone(position.press());self.assertEqual(calls,[])
                state['modifier']=False;state['foreground']=False;self.assertIsNone(position.press());self.assertEqual(calls,[])
                state['foreground']=True;state['raise_down']=True
                with self.assertRaises(OSError):position.press()
                self.assertEqual(calls,[1,3])
                # Ctrl+R: modifier down first, main key last, released in reverse.
                state['raise_down']=False;calls.clear();keys_sent.clear()
                binding['keys']=((0xA2,0x1D,0),(0x52,0x13,0));position=app.start_position_session(1,info,None,Path(tmp))
                self.assertTrue(position.press());self.assertEqual(keys_sent,[(0xA2,0),(0x52,0),(0x52,2),(0xA2,2)])
                binding.update(usable=False,hint='截图键 F12 与Steam 截图键冲突')
                while app.ui.qsize():app.ui.get_nowait()
                self.assertIsNone(app.start_position_session(1,info,None,Path(tmp)))
                kind,payload=app.ui.get_nowait()
                self.assertTrue(payload['unavailable']);self.assertIn('F12',payload['reason'])


if __name__=='__main__':unittest.main()
