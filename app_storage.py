"""Validated preferences and writable data location; no monitoring side effects."""
import json
import os
from pathlib import Path
import tempfile


def load_settings(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value,dict) else {}


def writable_data_directory(preferred, edition='V0.3'):
    preferred=Path(preferred)
    fallback=Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'TarkovHUDMonitor'/edition
    for candidate in (preferred,fallback):
        try:
            candidate.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryFile(dir=candidate):
                pass
            return candidate
        except OSError:
            if candidate==fallback:
                raise
    raise OSError('无法创建可写的数据目录')
