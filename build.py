"""Build V0.3 live-only Windows release. Run in a venv from requirements-build.txt."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--dependency-dir',type=Path,action='append',default=[],help='Optional existing Python package directory; normally use a venv.')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    if root.name=='build_tools':root=root.parent
    source=root/'TarkovHUDMonitor' if (root/'TarkovHUDMonitor').is_dir() else root
    output=(args.output_dir or root/'dist').resolve();output.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='id486-v03-build-'))
    stage=work/'stage';stage.mkdir()
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    broker=source/'assets/network/ID486NetworkBroker.exe';broker.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/reference:System.Web.Extensions.dll','/out:'+str(broker)]+[str(source/name) for name in ('network_broker.cs','network_routes.cs','network_domains.cs','network_policy.cs','network_prepared.cs','network_uu.cs')],check=True,cwd=root)
    for module in source.glob('*.py'):
        if module.name not in ('video_test.py','video_reader.py'):
            shutil.copy2(module,stage/module.name)
    assets=stage/'assets';assets.mkdir()
    omit={'emilia_head.png','emilia.ico','emilia.png','mascot_ui.png','mascot.png','subaru_clean.png','subaru_clean.ico','return_by_death.ico','yellow_mark_source.webp','Plix-Medium.otf','Plix-SemiBold.otf','fence_hood_master.png','fence_hood_master_v1.png'}
    for item in (source/'assets').rglob('*'):
        if item.is_file() and item.name not in omit:
            target=assets/item.relative_to(source/'assets');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(item,target)
    version=root/'build_tools/id486_v03_version.txt'
    if not version.exists():version=root/'version_info.txt'
    spec=work/'ID486-V0.3.spec'
    spec.write_text(f"""from PyInstaller.utils.hooks import collect_all
datas = {[(str(assets),'assets')]!r}
binaries = []
hiddenimports = ['_cffi_backend','dxcam.processor._numpy_kernels']
tmp=collect_all('tkinterdnd2');datas+=tmp[0];binaries+=tmp[1];hiddenimports+=tmp[2]
a=Analysis([{str(stage/'monitor.py')!r}],pathex=[{str(stage)!r}],binaries=binaries,datas=datas,hiddenimports=hiddenimports,excludes=['av','video_test','video_reader','matplotlib','scipy','pytest','IPython','notebook','pandas'],noarchive=False,optimize=1)
a.binaries=[e for e in a.binaries if 'opencv_videoio_ffmpeg' not in e[0].lower()]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='ID486模拟助手',debug=False,strip=False,upx=False,console=False,version={str(version)!r},icon={str(assets/'pmc_character.ico')!r})
""",encoding='utf-8')
    search=[str(p.resolve()) for p in args.dependency_dir]
    if os.environ.get('PYTHONPATH'):search.append(os.environ['PYTHONPATH'])
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(search))
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(work/'dist'),'--workpath',str(work/'pyinstaller'),str(spec)],check=True,cwd=root,env=env)
    exe=output/'ID486模拟助手.exe';shutil.copy2(work/'dist'/exe.name,exe)
    print('BUILT',exe,exe.stat().st_size,hashlib.sha256(exe.read_bytes()).hexdigest(),flush=True)
    print('BUILD_LOGS',work,flush=True)


if __name__=='__main__':main()
