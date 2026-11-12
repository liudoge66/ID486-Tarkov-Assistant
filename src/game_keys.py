"""Read the game's own screenshot binding and check it against this machine's shortcuts.

Read-only: never writes Control.ini, Steam config or the registry. The only
system side effect is a momentary RegisterHotKey/UnregisterHotKey probe.
"""
import json,os,re
from pathlib import Path

# Unity KeyCode name -> (virtual key, scan code, extended flag)
KEYS={}
for _ch,_scan in zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',(0x1E,0x30,0x2E,0x20,0x12,0x21,0x22,0x23,0x17,0x24,0x25,0x26,0x32,
                                                   0x31,0x18,0x19,0x10,0x13,0x1F,0x14,0x16,0x2F,0x11,0x2D,0x15,0x2C)):
    KEYS[_ch]=(ord(_ch),_scan,0)
for _n in range(10):
    KEYS['Alpha%d'%_n]=(0x30+_n,0x0B if _n==0 else 0x01+_n,0)
    KEYS['Keypad%d'%_n]=(0x60+_n,(0x52,0x4F,0x50,0x51,0x4B,0x4C,0x4D,0x47,0x48,0x49)[_n],0)
for _n in range(1,16):
    KEYS['F%d'%_n]=(0x6F+_n,(0x3A+_n if _n<=10 else {11:0x57,12:0x58,13:0x64,14:0x65,15:0x66}[_n]),0)
KEYS.update({
    'KeypadMultiply':(0x6A,0x37,0),'KeypadDivide':(0x6F,0x35,1),'KeypadMinus':(0x6D,0x4A,0),'KeypadPlus':(0x6B,0x4E,0),
    'KeypadPeriod':(0x6E,0x53,0),'KeypadEnter':(0x0D,0x1C,1),
    'Insert':(0x2D,0x52,1),'Delete':(0x2E,0x53,1),'Home':(0x24,0x47,1),'End':(0x23,0x4F,1),'PageUp':(0x21,0x49,1),'PageDown':(0x22,0x51,1),
    'UpArrow':(0x26,0x48,1),'DownArrow':(0x28,0x50,1),'LeftArrow':(0x25,0x4B,1),'RightArrow':(0x27,0x4D,1),
    'Space':(0x20,0x39,0),'Tab':(0x09,0x0F,0),'Return':(0x0D,0x1C,0),'Backspace':(0x08,0x0E,0),'Pause':(0x13,0x45,0),
    'Minus':(0xBD,0x0C,0),'Equals':(0xBB,0x0D,0),'LeftBracket':(0xDB,0x1A,0),'RightBracket':(0xDD,0x1B,0),'Backslash':(0xDC,0x2B,0),
    'Semicolon':(0xBA,0x27,0),'Quote':(0xDE,0x28,0),'BackQuote':(0xC0,0x29,0),'Comma':(0xBC,0x33,0),'Period':(0xBE,0x34,0),'Slash':(0xBF,0x35,0),
    'SysReq':(0x2C,0x37,1),'Print':(0x2C,0x37,1),'PrintScreen':(0x2C,0x37,1),'Print Screen':(0x2C,0x37,1),
    'LeftShift':(0xA0,0x2A,0),'RightShift':(0xA1,0x36,0),'LeftControl':(0xA2,0x1D,0),'RightControl':(0xA3,0x1D,1),
    'LeftAlt':(0xA4,0x38,0),'RightAlt':(0xA5,0x38,1),
})
MODIFIERS={'LeftShift':4,'RightShift':4,'LeftControl':2,'RightControl':2,'LeftAlt':1,'RightAlt':1}
# Keys that change system/game state on their own when sent.
BLOCKED=re.compile(r'^(?:Escape|CapsLock|Numlock|ScrollLock|(?:Left|Right)(?:Windows|Command|Apple)|Menu|Mouse\d+|Joystick.*|None)$')
LABELS={'KeypadMultiply':'小键盘*','KeypadDivide':'小键盘/','KeypadMinus':'小键盘-','KeypadPlus':'小键盘+','KeypadPeriod':'小键盘.',
        'KeypadEnter':'小键盘Enter','LeftShift':'左Shift','RightShift':'右Shift','LeftControl':'左Ctrl','RightControl':'右Ctrl',
        'LeftAlt':'左Alt','RightAlt':'右Alt','SysReq':'PrintScreen','Print':'PrintScreen','Print Screen':'PrintScreen','BackQuote':'`'}
# Windows shortcuts that never reach the game as a plain key press.
RESERVED={(1,0x09):'Alt+Tab',(1,0x1B):'Alt+Esc',(2,0x1B):'Ctrl+Esc',(6,0x1B):'Ctrl+Shift+Esc',(1,0x73):'Alt+F4',(1,0x20):'Alt+Space',(1,0x0D):'Alt+Enter'}
RECOMMEND='推荐小键盘*'


def label(names):
    def one(name):
        if name in LABELS:return LABELS[name]
        m=re.fullmatch(r'Keypad(\d)',name)
        if m:return '小键盘'+m[1]
        m=re.fullmatch(r'Alpha(\d)',name)
        return m[1] if m else name
    names=sorted(names,key=lambda n:n not in MODIFIERS)
    return '+'.join(one(n) for n in names)


def combo_label(combo):
    mods,vk=combo
    names=[s for bit,s in ((2,'Ctrl'),(1,'Alt'),(4,'Shift')) if mods&bit]
    key=next((label([n]) for n,v in KEYS.items() if v[0]==vk and n not in MODIFIERS),'VK%d'%vk)
    return '+'.join(names+[key])


def default_control_path():
    return Path(os.environ.get('APPDATA',''))/'Battlestate Games/Escape from Tarkov/Settings/Control.ini'


def bindings(data):
    """Every (action, keys) pair in Control.ini, including axis halves."""
    out=[]
    def walk(value,name=None):
        if isinstance(value,dict):
            # keyBinding variants may carry their own axisName (e.g. ScopeZoomIn
            # on "Mouse Scroll"); the action name is the enclosing keyName.
            name=value.get('keyName') or name or value.get('axisName')
            codes=value.get('keyCode')
            if isinstance(codes,list) and codes:out.append((str(name),tuple(str(c) for c in codes)))
            for key,item in value.items():
                if key!='keyCode':walk(item,name)
        elif isinstance(value,list):
            for item in value:walk(item,name)
    walk(data);return out


def screenshot_variants(data):
    return [keys for name,keys in bindings(data) if name=='MakeScreenshot']


STEAM_KEYS={'TAB':0x09,'SPACE':0x20,'INSERT':0x2D,'DELETE':0x2E,'HOME':0x24,'END':0x23,'PAGEUP':0x21,'PAGEDOWN':0x22,
            'PAD_MULTIPLY':0x6A,'PAD_DIVIDE':0x6F,'PAD_MINUS':0x6D,'PAD_PLUS':0x6B,'PAD_DECIMAL':0x6E}


def steam_combo(text):
    """Parse a Steam hotkey value such as 'Shift\\tKEY_TAB'; '' means disabled."""
    mods=0;vk=None
    for token in str(text).split():
        upper=token.upper()
        if upper in ('SHIFT','CTRL','CONTROL','ALT'):mods|={'SHIFT':4,'CTRL':2,'CONTROL':2,'ALT':1}[upper];continue
        name=upper[4:] if upper.startswith('KEY_') else upper
        m=re.fullmatch(r'F(\d{1,2})',name)
        if m and 1<=int(m[1])<=24:vk=0x6F+int(m[1])
        elif re.fullmatch(r'[A-Z0-9]',name):vk=ord(name)
        elif re.fullmatch(r'PAD_(\d)',name):vk=0x60+int(name[-1])
        elif name in STEAM_KEYS:vk=STEAM_KEYS[name]
    return None if vk is None else (mods,vk)


class SystemEnvironment:
    """Live lookups; tests pass a stand-in with the same two methods."""
    def __init__(self):self.cache={}

    def probe(self,combo):
        """True: free, False: another program registered it, None: unknown."""
        import ctypes as C
        from ctypes import wintypes as W
        U=C.WinDLL('user32',use_last_error=True)
        U.RegisterHotKey.argtypes=[W.HWND,C.c_int,W.UINT,W.UINT];U.UnregisterHotKey.argtypes=[W.HWND,C.c_int]
        if U.RegisterHotKey(None,0xB0E5,0x4000|combo[0],combo[1]):
            U.UnregisterHotKey(None,0xB0E5);return True
        return False if C.get_last_error()==1409 else None

    def steam_root(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam') as key:return Path(winreg.QueryValueEx(key,'SteamPath')[0])
        except OSError:return None

    def steam_shortcuts(self):
        """[(combo, purpose)] from every local Steam account; Steam defaults when unset."""
        root=self.steam_root()
        if root is None or not root.is_dir():return []
        found=[]
        for path in sorted((root/'userdata').glob('*/config/localconfig.vdf')):
            try:stamp=path.stat().st_mtime_ns
            except OSError:continue
            cached=self.cache.get(path)
            if not cached or cached[0]!=stamp:
                try:text=path.read_text(encoding='utf-8',errors='replace')
                except OSError:continue
                values={}
                for setting in ('InGameOverlayScreenshotHotKey','InGameOverlayShortcutKey'):
                    m=re.search(r'"'+setting+r'"\s+"([^"]*)"',text)
                    values[setting]=None if m is None else m[1]
                cached=self.cache[path]=(stamp,values)
            values=cached[1]
            for setting,default,purpose in (('InGameOverlayScreenshotHotKey','KEY_F12','Steam 截图键'),
                                            ('InGameOverlayShortcutKey','Shift\tKEY_TAB','Steam 界面快捷键')):
                combo=steam_combo(default if values[setting] is None else values[setting])
                if combo and (combo,purpose) not in found:found.append((combo,purpose))
        return found


SYSTEM=SystemEnvironment()


def check_variant(names,others,tool_combo,env):
    """Problems block automatic input; warnings are shown but still allow it."""
    problems=[];warnings=[];text=label(names)
    blocked=[n for n in names if BLOCKED.match(n)]
    if blocked:
        return dict(names=names,label=text,keys=(),combo=None,problems=['是助手不能发送的按键（'+label(blocked[:1])+'）'],warnings=[])
    unknown=[n for n in names if n not in KEYS]
    if unknown:
        return dict(names=names,label=text,keys=(),combo=None,problems=['无法识别（'+unknown[0]+'）'],warnings=[])
    mods=[n for n in names if n in MODIFIERS];mains=[n for n in names if n not in MODIFIERS]
    if len(mains)>1 or not mains and len(mods)!=1:
        return dict(names=names,label=text,keys=(),combo=None,problems=['不是单键或 Ctrl/Alt/Shift 组合'],warnings=[])
    if not mains:mains,mods=mods,[]
    sequence=tuple(mods)+tuple(mains)
    combo=(sum({MODIFIERS[n] for n in mods}),KEYS[mains[0]][0])
    if tool_combo is not None and tuple(tool_combo)==combo:problems.append('与助手开关快捷键相同')
    if combo[1]==0x2C:problems.append('会触发 Windows 截图')
    if combo in RESERVED:problems.append('是 Windows 系统快捷键 '+RESERVED[combo])
    for other,purpose in env.steam_shortcuts():
        if other==combo:problems.append('与'+purpose+'冲突')
    same={frozenset(sequence)}
    for action,keys in others:
        if frozenset(keys) in same:problems.append('与游戏内「'+action+'」键位相同')
    for mod in mods:
        for action,keys in others:
            if keys==(mod,):warnings.append('按下时会带上'+label([mod])+'，可能同时触发「'+action+'」')
    if not problems and mains[0] not in MODIFIERS and combo[1]!=0x2C:
        state=env.probe(combo)
        if state is False:problems.append('已被其他程序注册为全局快捷键')
    seen=set();problems=[p for p in problems if not (p in seen or seen.add(p))]
    return dict(names=names,label=text,keys=tuple(KEYS[n] for n in sequence),combo=combo,problems=problems,warnings=warnings)


def analyze(preferences=None,control_path=None,tool_combo=None,env=None):
    """Pick the first conflict-free screenshot variant and explain any problem."""
    env=env or SYSTEM;preferences=preferences or {}
    path=Path(control_path) if control_path else default_control_path()
    data=None;error=None
    try:data=json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:error='未找到游戏键位文件，请先启动一次游戏'
    except (OSError,ValueError):error='游戏键位文件无法读取'
    override=preferences.get('lighthouse_screenshot_key')
    if override is not None:
        variants=[tuple(str(n) for n in override) if isinstance(override,(list,tuple)) else (str(override),)];source='settings'
    elif data is None:
        return dict(usable=False,keys=(),combo=None,label='',problems=[error],warnings=[],hint=error,source='game')
    else:variants=screenshot_variants(data);source='game'
    if not variants:
        hint='游戏未设置截图键，请在游戏控制设置中绑定（'+RECOMMEND+'）'
        return dict(usable=False,keys=(),combo=None,label='',problems=['未设置'],warnings=[],hint=hint,source=source)
    others=[b for b in bindings(data) if b[0]!='MakeScreenshot'] if data is not None else []
    checked=[check_variant(tuple(v),others,tool_combo,env) for v in variants]
    chosen=next((c for c in checked if not c['problems']),checked[0])
    usable=not chosen['problems']
    if usable:hint='截图键 '+chosen['label']+' · '+chosen['warnings'][0] if chosen['warnings'] else ''
    else:hint='截图键 '+chosen['label']+' '+chosen['problems'][0]+'，请在游戏控制设置中更换（'+RECOMMEND+'）'
    return dict(chosen,usable=usable,hint=hint,source=source,checked=checked)
