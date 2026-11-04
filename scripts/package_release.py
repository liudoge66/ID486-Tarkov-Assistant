"""Assemble an allowlisted clean release; never copy live user data."""
import hashlib,json,shutil,zipfile,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'dist'
temporary=tempfile.TemporaryDirectory(prefix='id486-clean-release-')
PACKAGE=Path(temporary.name)/'ID486-V0.3-Windows'
PACKAGE.mkdir(parents=True,exist_ok=True)
shutil.copy2(OUT/'ID486模拟助手.exe',PACKAGE/'ID486模拟助手.exe')
for name in ('README.md',):
    shutil.copy2(ROOT/name,PACKAGE/name)
for folder in ('docs',):
    shutil.copytree(ROOT/folder,PACKAGE/folder,dirs_exist_ok=True)
shutil.copytree(ROOT/'src/assets/capture',PACKAGE/'capture-licenses',ignore=shutil.ignore_patterns('*.dll'),dirs_exist_ok=True)
archive=OUT/'ID486-V0.3-Windows.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(PACKAGE.rglob('*')):
        if p.is_file():z.write(p,PACKAGE.name+'/'+p.relative_to(PACKAGE).as_posix())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    forbidden={'settings.json','sound_settings.json','hotkey_settings.json','运行记录.txt'}
    assert not any(Path(n).name in forbidden or '数据/' in n or 'network_runtime/' in n or 'diagnostics/' in n or n.endswith(('.mp4','.jsonl')) for n in z.namelist())
sums=OUT/'SHA256SUMS.txt'
sums.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in (archive,OUT/'ID486模拟助手.exe')),encoding='utf-8')
print(json.dumps(dict(zip=archive.name,bytes=archive.stat().st_size,zip_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())))
temporary.cleanup()
