import queue
import threading
import types
import unittest
from unittest.mock import patch
from test_zone_ui import make_app,mod

class RestartWaitTests(unittest.TestCase):
    def app(self):
        app=make_app();app.closed=False;app.arming=False
        app.lock=threading.RLock();app.pending=queue.Queue()
        app.stops=[];app.events=[];app.gate_cancels=[]
        app.gate=types.SimpleNamespace(cancel=lambda:app.gate_cancels.append(True))
        app.stop_network_session=lambda:app.stops.append(True)
        app.event=lambda name,**kw:app.events.append((name,kw))
        app.exit_enabled=True
        return app

    def test_dead_binding_invalidated_and_silent_wait(self):
        app=self.app()
        self.assertTrue(app.queue_game_restart(1,(101,202,1920,1080),True))
        self.assertEqual(app.token,2)
        self.assertEqual(app.pending.get_nowait(),(2,None,True,True))
        self.assertEqual(app.stops,[True]);self.assertEqual(app.gate_cancels,[True])
        self.assertTrue(app.active and app.arming and app.zone_hold)
        app.closed=True;app.tick()
        self.assertEqual(app.state_head.get(),'游戏已退出 · 等待重新启动')
        self.assertEqual(app.zone_view['avatar'],'sleep')
        self.assertEqual((app.beeps,app.plays),([],[]))

    def test_old_worker_cannot_requeue(self):
        app=self.app();app.token=2
        self.assertFalse(app.queue_game_restart(1,(1,2,3,4),False))
        self.assertTrue(app.pending.empty());self.assertFalse(app.stops)

    def test_manual_stop_cancels_wait_and_stale_request(self):
        app=self.app();app.queue_game_restart(1,(1,2,3,4),False)
        token=app.token;app.cancel('manual')
        with patch.object(mod,'target') as target:
            self.assertIsNone(app.wait_game_restart(token));target.assert_not_called()
        self.assertFalse(app.active);self.assertFalse(app.arming)

    def test_foreground_game_reacquired_after_absence(self):
        app=self.app();info=(77,888,3840,2160)
        with patch.object(mod,'target',side_effect=[None,None,info]),patch.object(mod.time,'sleep') as sleep:
            self.assertEqual(app.wait_game_restart(1),info)
            self.assertEqual(sleep.call_count,2)

    def test_stop_during_wait(self):
        app=self.app()
        with patch.object(mod,'target',return_value=None),patch.object(mod.time,'sleep',side_effect=lambda _:app.cancel('manual')):
            self.assertIsNone(app.wait_game_restart(1))

    def test_app_close_cancels_wait(self):
        app=self.app();app.closed=True
        with patch.object(mod,'target') as target:
            self.assertIsNone(app.wait_game_restart(1));target.assert_not_called()

    def test_new_game_online_rejected_before_capture(self):
        from detector import Gate
        app=self.app();app.gate=Gate();app.capture_hz=30
        app.detector=types.SimpleNamespace(reset_layout=lambda:None)
        app.pending.put((1,None,False,True))
        app.wait_game_restart=lambda token:(77,888,1920,1080)
        game=types.SimpleNamespace(handle=123,close=lambda:None)
        rejected=[]
        def reject(token,reason):
            rejected.append((token,reason));app.closed=True
        app.reject_raid_scope=reject
        with patch.object(mod,'GameProcess',return_value=game),patch.object(mod,'game_rejection',return_value=mod.ONLINE_CAPTION),patch.object(mod,'SharedCapture') as capture:
            app.worker();capture.assert_not_called()
        self.assertEqual(rejected,[(1,mod.ONLINE_CAPTION)])
        self.assertFalse(app.plays)

if __name__=='__main__':unittest.main()
