"""Raid lifecycle from the game's application log (raid_scope.lifecycle / SessionReader.lifecycle).

11-24 02:38: a video frame was taken as a raid HUD 5 s after the game started; the game log
only had 'Session mode: Pve'. Arming and standby resume now require GameStarted.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import raid_scope
from raid_scope import SessionReader,lifecycle

LOGS=Path(r'F:\SteamLibrary\steamapps\common\Escape from Tarkov\build\Logs')

def line(t,text):
    return f'2026-11-24 {t}|1.3.0.2.48779|Info|application|{text}\n'

MENU_ONLY=line('02:37:51.714','Application awaken')+line('02:38:03.835','Session mode: Pve')
RAID=(MENU_ONLY+line('02:39:00.000','PrepareSelectedProfileLocally ProfileId:x AccountId:y')
      +line('02:40:17.646','MatchingCompleted:0 real:0 diff:0')
      +line('02:40:18.040','scene preset path:maps/customs_preset.bundle rcid:bigmap.scenespreset.asset')
      +line('02:40:40.172','LocationLoaded:18.76 real:22.52 diff:3.76')
      +line('02:41:24.278','GameStarting:55.86(0) real:66.63(0) diff:10.76')
      +line('02:41:24.278','GameStarted:55.86(0) real:66.63(0) diff:10.76'))


class LifecycleLines(unittest.TestCase):
    def test_markers(self):
        self.assertEqual(lifecycle(line('0','GameStarted:1')),'in_raid')
        self.assertEqual(lifecycle(line('0','Session mode: Pve')),'menu')
        self.assertEqual(lifecycle(line('0','PrepareSelectedProfileLocally ProfileId:x')),'menu')
        for text in ('MatchingCompleted:0','LocationLoaded:1','GameStarting:1','scene preset path:maps/x.bundle','Matching with group id: 1'):
            self.assertEqual(lifecycle(line('0',text)),'loading',text)
        self.assertIsNone(lifecycle(line('0','NVIDIA Reflex is available')))
        self.assertIsNone(lifecycle('2026-11-24 00:00:00.000|x|Info|backend|GameStarted:1'))


class ReaderLifecycle(unittest.TestCase):
    def reader(self,text):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'2026.11.24_2-37-50_1.3.0.2.48779 application_000.log'
        path.write_text(text,encoding='utf-8')
        return SessionReader(folder.name),path

    def test_fresh_game_is_menu(self):
        reader,_=self.reader('')
        self.assertEqual(reader.lifecycle(),'menu')
        reader,_=self.reader(MENU_ONLY)
        self.assertEqual(reader.lifecycle(),'menu')

    def test_raid_progression_and_back_to_menu(self):
        reader,path=self.reader(MENU_ONLY)
        self.assertEqual(reader.lifecycle(),'menu')
        with path.open('a',encoding='utf-8') as f:f.write(RAID[len(MENU_ONLY):])
        self.assertEqual(reader.lifecycle(),'in_raid')
        with path.open('a',encoding='utf-8') as f:f.write(line('02:59:00.000','PrepareSelectedProfileLocally ProfileId:x AccountId:y'))
        self.assertEqual(reader.lifecycle(),'menu')

    def test_half_line_waits(self):
        reader,path=self.reader(MENU_ONLY)
        with path.open('a',encoding='utf-8') as f:f.write(line('02:41:24.278','GameStarted:1').rstrip('\n'))
        self.assertEqual(reader.lifecycle(),'menu')
        with path.open('a',encoding='utf-8') as f:f.write('\n')
        self.assertEqual(reader.lifecycle(),'in_raid')

    def test_online_rejection_unchanged(self):
        reader,_=self.reader(RAID)
        self.assertIsNone(reader.poll())


class GameLifecycle(unittest.TestCase):
    def test_missing_logs_are_unknown(self):
        class Game:
            creation_unix=1.0;executable=Path(tempfile.gettempdir())/'nope'/'EscapeFromTarkov.exe'
        self.assertEqual(raid_scope.game_lifecycle(Game()),'unknown')

    def test_no_creation_time_is_unknown(self):
        class Game:creation_unix=None
        self.assertEqual(raid_scope.game_lifecycle(Game()),'unknown')


@unittest.skipUnless((LOGS/'log_2026.11.24_2-37-50_1.3.0.2.48779').is_dir(),'game logs not on this machine')
class RealSessions(unittest.TestCase):
    def test_false_trigger_session_never_reached_a_raid(self):
        self.assertEqual(SessionReader(LOGS/'log_2026.11.24_2-37-50_1.3.0.2.48779').lifecycle(),'menu')

    def test_labs_session_was_in_raid(self):
        self.assertEqual(SessionReader(LOGS/'log_2026.11.22_20-09-52_1.3.0.2.48779').lifecycle(),'in_raid')


if __name__=='__main__':
    unittest.main()
