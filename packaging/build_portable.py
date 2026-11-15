"""Build a clean Windows x64 portable archive from this source checkout.
Run under a workspace venv containing requirements-dev.txt. Dependency source
is the explicitly selected interpreter, not the global user site-packages.
Never copy an existing dist, bin profile, data directory, key or test output.
"""
from __future__ import annotations
import argparse,hashlib,io,json,os,re,shutil,subprocess,sys,tempfile,urllib.request,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PYTHON_VERSION='3.12.10'
PYTHON_URL=f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'


def run(args):
    subprocess.run([str(x) for x in args],cwd=ROOT,check=True)


def dependencies(interpreter):
    # Only project runtime roots; include each package's license and dist-info.
    script='''import importlib.metadata as m,json
from packaging.requirements import Requirement
queue=['fastapi','uvicorn','requests','pydantic','cryptography'];seen=set();result=[]
while queue:
 name=queue.pop();d=m.distribution(name);key=d.metadata['Name'].lower().replace('_','-')
 if key in seen:continue
 seen.add(key)
 result.append({'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License') or 'SEE INCLUDED LICENSE','files':[{'relative':str(f),'source':str(d.locate_file(f))} for f in d.files or [] if '..' not in f.parts and '__pycache__' not in f.parts]})
 for text in d.requires or []:
  r=Requirement(text)
  if r.marker is None or r.marker.evaluate({'extra':''}):queue.append(r.name)
print(json.dumps(result))
'''
    p=subprocess.run([str(interpreter),'-c',script],check=True,capture_output=True,text=True,encoding='utf-8',cwd=ROOT)
    return json.loads(p.stdout)


def build(interpreter: Path, output: Path, archive_path: Path | None):
    if os.name!='nt':raise SystemExit('Portable WinUI build requires Windows x64')
    interpreter=interpreter.resolve()
    if not interpreter.is_file():raise SystemExit('Explicit Python interpreter does not exist')
    abi=subprocess.check_output([str(interpreter),'-c','import sys,struct;print(f"{sys.version_info.major}.{sys.version_info.minor}/{struct.calcsize(chr(80))*8}")'],text=True).strip()
    if abi!='3.12/64':raise SystemExit('Embedded runtime requires CPython3.12 x64 dependencies')
    run(['dotnet','build','desktop/MusicBox.Desktop/MusicBox.Desktop.csproj','-c','Release'])
    binary=ROOT/'desktop/MusicBox.Desktop/bin/Release/net8.0-windows10.0.19041.0/win-x64'
    output=output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():raise SystemExit('Output exists; select an absent output directory (never overwrite a running/user-data folder)')
    stage=Path(tempfile.mkdtemp(prefix='mb-release-',dir=output.parent))
    try:
        app=stage/'MusicBox';app.mkdir()
        for file in binary.rglob('*'):
            rel=file.relative_to(binary)
            if any(p in {'data','__pycache__','EBWebView'} or p.endswith('.WebView2') for p in rel.parts):continue
            if not file.is_file() or file.suffix.lower() in {'.pdb','.log','.db','.sqlite3','.pyc'}:continue
            if file.suffix.lower() not in {'.exe','.dll','.mui','.html','.json','.xbf','.pri','.winmd','.manifest','.png','.ico','.txt','.xml'}:continue
            dest=app/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,dest)
        runtime=app/'runtime';runtime.mkdir()
        request=urllib.request.Request(PYTHON_URL,headers={'User-Agent':'MusicBox-source-builder'})
        with urllib.request.urlopen(request,timeout=60) as response:blob=response.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for n in z.namelist():
                if not (runtime/n).resolve().is_relative_to(runtime.resolve()):raise ValueError('Unsafe runtime archive path')
            z.extractall(runtime)
        (runtime/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\nimport site\n',encoding='ascii')
        deps=dependencies(interpreter);site=runtime/'Lib/site-packages';site.mkdir(parents=True)
        for dist in deps:
            for f in dist.pop('files'):
                rel=Path(f['relative']);source=Path(f['source']);dest=site/rel
                if not dest.resolve().is_relative_to(site.resolve()):raise ValueError('Unsafe dependency path')
                if source.is_file() and source.suffix!='.pyc':dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        shutil.copytree(ROOT/'musicbox',runtime/'musicbox',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in ('LICENSE','README.md','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT/name,app/name)
        shutil.copytree(ROOT/'LICENSES',app/'LICENSES')
        # Preserve package licenses/notices from the exact NuGet restore graph.
        assets=json.loads((ROOT/'desktop/MusicBox.Desktop/obj/project.assets.json').read_text(encoding='utf-8-sig'))
        package_roots=[Path(p) for p in assets.get('packageFolders',{})]
        notices=[]
        for key,lib in assets.get('libraries',{}).items():
            if lib.get('type')!='package':continue
            package=next((r/lib['path'] for r in package_roots if (r/lib['path']).is_dir()),None)
            item={'package':key,'licenseFiles':[]}
            if package:
                for file in package.rglob('*'):
                    if file.is_file() and re.search(r'license|notice|copying',file.name,re.I) and file.stat().st_size<2_000_000:
                        rel=file.relative_to(package);dest=app/'nuget-licenses'/key.replace('/','_')/rel
                        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,dest);item['licenseFiles'].append(rel.as_posix())
                for nuspec in package.glob('*.nuspec'):
                    dest=app/'nuget-licenses'/key.replace('/','_')/nuspec.name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(nuspec,dest)
            notices.append(item)
        # Public manifest omits developer interpreter and absolute package paths.
        manifest={'pythonVersion':PYTHON_VERSION,'pythonSource':PYTHON_URL,'pythonArchiveSHA256':hashlib.sha256(blob).hexdigest(),
                  'runtimeDependencies':deps,'nugetPackages':notices,'signed':False,'webSignatureImplementationIncluded':False}
        (app/'bundle-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        (app/'portable-mode').write_text('Portable data lives in ./data; never publish that directory.\n',encoding='utf-8')
        shutil.move(str(app),str(output))
        if archive_path:
            archive_path=archive_path.resolve();archive_path.parent.mkdir(parents=True,exist_ok=True)
            if archive_path.exists():raise SystemExit('Archive exists; select a new path')
            with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                for file in sorted(output.rglob('*')):
                    if file.is_file():z.write(file,Path('MusicBox')/file.relative_to(output))
            archive_path.with_suffix('.sha256').write_text(hashlib.sha256(archive_path.read_bytes()).hexdigest()+'  '+archive_path.name+'\n',encoding='ascii')
        print('Portable output:',output)
    finally:shutil.rmtree(stage)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--python',type=Path,required=True);p.add_argument('--output',type=Path,default=ROOT/'dist/MusicBox');p.add_argument('--archive',type=Path,default=ROOT/'dist/MusicBox-portable-unsigned.zip');p.add_argument('--no-archive',action='store_true')
    args=p.parse_args();build(args.python,args.output,None if args.no_archive else args.archive)
