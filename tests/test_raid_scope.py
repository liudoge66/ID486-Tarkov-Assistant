import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from raid_scope import rejection,SessionReader,CAPTION

def app(text):
    return '|application|'+text

ONLINE=app('TRACE-NetworkGameCreate Location: Woods, RaidMode: Online')
SCENE=app('scene preset path: maps/Woods_preset.bundle')

class RaidScopeTests(unittest.TestCase):
    def test_unknown_allowed(self):
        self.assertIsNone(rejection([]))

    def test_maps_alone_allowed(self):
        for name in ('icebreaker','terminal','tarkovstreets','labyrinth'):
            self.assertIsNone(rejection([app('scene preset path: maps/'+name+'.bundle')]))

    def test_online_and_case(self):
        for mode in ('RaidMode: Online','raidMode:Online','RaidMode: online'):
            self.assertEqual(rejection([app(mode)]),CAPTION)

    def test_local_does_not_override(self):
        self.assertEqual(rejection([ONLINE,'|backend|---> Request HTTPS /client/match/local/start']),CAPTION)

    def test_first_scene_does_not_erase_online(self):
        self.assertEqual(rejection([ONLINE,SCENE]),CAPTION)

    def test_scene_then_online(self):
        self.assertEqual(rejection([SCENE,ONLINE]),CAPTION)

    def test_new_match_resets(self):
        for marker in ('Matching with group id: x','GameEnded:','GameStopped:'):
            self.assertIsNone(rejection([ONLINE,app(marker)]))

    def test_transfer_resets(self):
        self.assertIsNone(rejection([SCENE,ONLINE,app('scene preset path: maps/Lighthouse.bundle')]))

    def test_same_map_reload_resets(self):
        self.assertIsNone(rejection([SCENE,ONLINE,SCENE]))

    def test_new_online_after_transfer(self):
        self.assertEqual(rejection([SCENE,ONLINE,app('scene preset path: maps/Lighthouse.bundle'),app('RaidMode: Online')]),CAPTION)

    def test_partial_and_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'application_000.log'
            reader=SessionReader(folder)
            path.write_text('2026-11-11 10:00:00.000'+ONLINE,encoding='utf-8')
            self.assertIsNone(reader.poll())
            with path.open('a',encoding='utf-8') as f:f.write('\n')
            self.assertEqual(reader.poll(),CAPTION)
            self.assertEqual(reader.poll(),CAPTION)
            (Path(folder)/'application_001.log').write_text('2026-11-11 10:01:00.000'+app('GameEnded:')+'\n',encoding='utf-8')
            self.assertIsNone(reader.poll())

    def test_bootstrap_beyond_old_tail_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'application_000.log'
            path.write_text('2026-11-11 10:00:00.000'+ONLINE+'\n'+('irrelevant\n'*60000),encoding='utf-8')
            self.assertEqual(SessionReader(folder).poll(),CAPTION)

if __name__=='__main__':unittest.main()
