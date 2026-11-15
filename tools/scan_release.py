"""Conservative local release scan. Prints rule/path/line, never matched secrets.
Default ignores generated development trees; --strict rejects them too.
No network calls and no inspection outside the selected repository root.
"""
from pathlib import Path
import argparse, re, json, hashlib

IGNORED = {'.git','.venv','venv','bin','obj','__pycache__','.pytest_cache','_scratch','dist','build','.vs','*.egg-info'}
FORBIDDEN_DIRS = {'data','WebViewLogin','EBWebView','node_modules','debugtools','attachments'}
FORBIDDEN_NAMES = {'.secret.key','.env','Cookies','Cookies-journal','Login Data','Local State','History','playback-sources.json'}
FORBIDDEN_EXTS = {'.db','.sqlite','.sqlite3','.pfx','.p12','.key','.pem','.dmp','.har','.pcap','.pcapng','.saz','.log','.pyc','.exe','.dll','.pdb'}
RULES = {
 'personal-windows-path': r'[A-Za-z]:[\\/]+Users[\\/]+(?!Public(?:[\\/]|\b))[^\s\x22\x27<>]+',
 'personal-unix-path': r'/(?:Users|home)/[A-Za-z0-9_.-]+/',
 'private-key': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----',
 'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
 'cloud-key': r'\bAKIA[0-9A-Z]{16}\b',
 'bearer-token': r'Bearer\s+eyJ[A-Za-z0-9_\-.]{30,}',
 'jwt': r'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b',
 'signed-audio-url': r'https?://[^\s\x22\x27<>]+[?&](?:vkey|authst|access_token|musickey)=[A-Za-z0-9_%+\-/]{16,}',
}
TEXT_EXTS={'.py','.cs','.xaml','.csproj','.xml','.json','.yml','.yaml','.toml','.md','.txt','.ps1','.iss','.html','.js','.css','.manifest','.example','.ini'}

def scan(root: Path, strict=False):
    issues=[];count=0;manifest=[]
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root)
        generated=any(part in IGNORED or part.endswith('.egg-info') for part in rel.parts)
        if generated and not strict: continue
        if p.is_symlink():
            issues.append({'rule':'symlink','path':rel.as_posix()});continue
        if not p.is_file():continue
        count+=1
        if (generated or any(x in FORBIDDEN_DIRS or x.endswith('.WebView2') for x in rel.parts)
            or p.name in FORBIDDEN_NAMES or p.suffix.lower() in FORBIDDEN_EXTS
            or p.name.endswith(('.sqlite3-wal','.sqlite3-shm','.db-wal','.db-shm'))):
            issues.append({'rule':'forbidden-artifact','path':rel.as_posix()})
        raw=p.read_bytes();manifest.append({'path':rel.as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        if p.suffix.lower() not in TEXT_EXTS and p.name not in {'LICENSE','.gitignore','.gitattributes','.editorconfig'}:continue
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            issues.append({'rule':'invalid-utf8','path':rel.as_posix()});continue
        for number,line in enumerate(text.splitlines(),1):
            for name,pattern in RULES.items():
                if re.search(pattern,line):issues.append({'rule':name,'path':rel.as_posix(),'line':number})
    return {'filesScanned':count,'issues':issues,'clean':not issues,'manifest':manifest}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--strict',action='store_true');parser.add_argument('--report',type=Path)
    args=parser.parse_args();result=scan(args.root.resolve(),args.strict)
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='manifest'},ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['clean'] else 1)
