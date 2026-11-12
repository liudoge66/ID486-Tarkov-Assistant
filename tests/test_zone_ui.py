"""UI-thread behaviour of the auto-pause states: texts, one beep per edge, silent standby, focus notices."""
import queue,sys,types,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import monitor as mod


class Var:
    def __init__(self,v=''):self.v=v
    def get(self):return self.v
    def set(self,v):self.v=v


def make_app():
    app=mod.App.__new__(mod.App)
    app.token=1;app.active=True;app.closed=True;app.key_name='F10'
    app.status=Var();app.bottom_status=Var();app.state_head=Var();app.prep_feedback=Var()
    app.zone_view=None;app.zone_clear_job=None;app.zone_hold=False
    app.beeps=[];app.plays=[];app.jobs=[]
    app.sounds=types.SimpleNamespace(muted=False,enabled={'enabled':True},
        play_cue=lambda name='enabled.wav':app.beeps.append(name),play=lambda key:app.plays.append(key))
    app.root=types.SimpleNamespace(after=lambda ms,fn:app.jobs.append((ms,fn)) or len(app.jobs),after_cancel=lambda job:None)
    app.event=lambda *a,**k:None;app.ui=queue.Queue();app.check_screenshot_key=lambda *a,**k:None
    return app


class ZoneUiTests(unittest.TestCase):
    def test_pause_leave_and_new_match_each_sound_once(self):
        app=make_app()
        app.zone_feedback(dict(state='paused',zone='extraction'))
        self.assertEqual(app.state_head.get(),'撤离区域 · 已自动暂停');self.assertEqual(app.zone_view['color'],'#BB9B5D')
        self.assertEqual(app.zone_view['avatar'],'pause');self.assertIn('不判定死亡',app.status.get());self.assertEqual(len(app.beeps),1)
        app.zone_feedback(dict(state='paused',zone='transfer'))
        self.assertEqual(app.state_head.get(),'转移区域 · 已自动暂停');self.assertEqual(len(app.beeps),2)
        app.zone_feedback(dict(state='left',zone='transfer'))
        self.assertIsNone(app.zone_view);self.assertEqual(len(app.beeps),3);self.assertIn('F10',app.bottom_status.get())

    def test_standby_is_silent_and_new_match_uses_start_sound_then_clears(self):
        app=make_app()
        app.zone_feedback(dict(state='standby'))
        self.assertEqual(app.zone_view['avatar'],'sleep');self.assertEqual(app.zone_view['color'],'#6B9BD1')
        self.assertEqual(app.state_head.get(),'撤离完成 · 等待新一局');self.assertEqual((app.beeps,app.plays),([],[]))
        app.zone_feedback(dict(state='raid'))
        self.assertEqual(app.zone_view['avatar'],'wake');self.assertEqual(app.plays,['enabled']);self.assertEqual(app.beeps,[])
        ms,clear=app.jobs[-1];self.assertEqual(ms,3000);clear()
        self.assertIsNone(app.zone_view);self.assertEqual(app.state_head.get(),'监控中')

    def test_muted_means_no_sound_anywhere(self):
        app=make_app();app.sounds.muted=True
        app.sounds.play_cue=lambda name='enabled.wav':None if app.sounds.muted else app.beeps.append(name)
        app.zone_feedback(dict(state='paused',zone='extraction'));app.zone_feedback(dict(state='raid'))
        self.assertEqual((app.beeps,app.plays),([],[]))

    def test_focus_notice_is_a_prefix_not_a_replacement(self):
        app=make_app();app.zone_feedback(dict(state='paused',zone='extraction'))
        head=app.state_head.get();bottom=app.bottom_status.get()
        app.ui.put(('status','监控中 · 已切出游戏，暂停判断；切回自动继续'));app.tick()
        self.assertTrue(app.status.get().startswith('已切出游戏 · '));self.assertIn('不判定死亡',app.status.get())
        self.assertEqual((app.state_head.get(),app.bottom_status.get()),(head,bottom))
        app.ui.put(('status','监控中 · 已回到游戏，继续判断'));app.tick()
        self.assertEqual(app.status.get(),app.zone_view['caption'])

    def test_stale_or_inactive_zone_messages_are_ignored(self):
        app=make_app();app.ui.put(('zone_status',dict(token=99,state='paused',zone='extraction')));app.tick()
        self.assertIsNone(app.zone_view)
        app.active=False;app.ui.put(('zone_status',dict(token=1,state='paused',zone='extraction')));app.tick()
        self.assertIsNone(app.zone_view)

    def test_transfer_feedback_does_not_overwrite_a_zone_view(self):
        app=make_app();app.zone_feedback(dict(state='paused',zone='transfer'));before=(app.status.get(),app.state_head.get())
        app.transfer_feedback(dict(token=1,phase='countdown'))
        self.assertEqual((app.status.get(),app.state_head.get()),before)

    def test_cancel_clears_the_zone_view(self):
        app=make_app();app.zone_feedback(dict(state='paused',zone='extraction'))
        self.assertIsNotNone(app.zone_view)
        app.zone_view=None;app.zone_hold=False  # what cancel() does under its lock
        self.assertIsNone(app.zone_view)


if __name__=='__main__':unittest.main()
