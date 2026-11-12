"""Read-only launch exclusions based on the current game's session logs."""
import re
import time
from pathlib import Path
from lighthouse_position import APPLICATION, bind_session, map_event

TITLE='无法启动 · 线上战局'
CAPTION='当前战局为 Online，仅支持本地 PvE'
STAMP=re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})')
ONLINE=re.compile(r'\bRaidMode\s*:\s*Online\b',re.I)

def rejection(lines):
    current=None;online=False;scene_seen=False
    for line in lines:
        event=map_event(line)
        if event and event[0]=='reset':
            current=None;online=scene_seen=False
        elif event and event[0]=='map':
            name=event[1].lower()
            scene='scene preset path:' in line.lower()
            if current is not None and (name!=current or scene and scene_seen):online=scene_seen=False
            if scene:scene_seen=True
            current=name
        if '|application|' in line and ONLINE.search(line):
            online=True
    if online:return CAPTION
    return None

class SessionReader:
    """Backfill once, then tail complete lines; retain only mode/lifecycle cues."""
    def __init__(self,folder):
        self.folder=Path(folder);self.files={}

    def poll(self):
        paths=[]
        for path in self.folder.glob('*.log'):
            m=APPLICATION.search(path.name)
            if m:paths.append((int(m[1] or 0),path))
        for _,path in sorted(paths):
            size=path.stat().st_size
            offset,pending,rows=self.files.get(path,(0,b'',[]))
            if size<offset:offset,pending,rows=0,b'',[]
            with path.open('rb') as stream:
                stream.seek(offset)
                for raw in iter(lambda:stream.read(256*1024),b''):
                    lines=(pending+raw).split(b'\n');pending=lines.pop()
                    for raw_line in lines:
                        line=raw_line.decode('utf-8',errors='replace')
                        stamp=STAMP.match(line)
                        if stamp and (map_event(line) or ONLINE.search(line)):
                            rows.append((stamp[1],line))
                offset=stream.tell()
            self.files[path]=(offset,pending,rows)
        rows=[row for _,_,entries in self.files.values() for row in entries]
        rows.sort(key=lambda row:row[0])
        return rejection(line for _,line in rows)

import threading
_readers={}
_reader_lock=threading.Lock()

def session_rejection(folder):
    with _reader_lock:
        key=str(Path(folder).resolve())
        if key not in _readers:
            if len(_readers)>=4:_readers.pop(next(iter(_readers)))
            _readers[key]=SessionReader(folder)
        return _readers[key].poll()


def game_rejection(game):
    if getattr(game,'creation_unix',None) is None:return None
    try:
        folder=bind_session(game.executable.parent/'Logs',game.creation_unix,time.time())
        return session_rejection(folder)
    except OSError:
        return None
