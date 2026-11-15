"""Verify the release guard actually detects synthetic sensitive artifacts."""
from pathlib import Path
import importlib.util

spec=importlib.util.spec_from_file_location('release_scan',Path(__file__).resolve().parents[1]/'tools/scan_release.py')
scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)


def test_release_scan_detects_key_profile_and_personal_path(tmp_path):
    (tmp_path/'.secret.key').write_bytes(b'SYNTHETIC_NOT_A_REAL_KEY')
    profile=tmp_path/'browser.WebView2';profile.mkdir();(profile/'Cookies').write_bytes(b'SYNTHETIC')
    personal='C:'+chr(92)+'Users'+chr(92)+'example-user'+chr(92)+'file.txt'
    (tmp_path/'sample.py').write_text('path='+repr(personal),encoding='utf-8')
    result=scanner.scan(tmp_path,strict=True)
    assert not result['clean']
    assert any(x['rule']=='forbidden-artifact' and x['path']=='.secret.key' for x in result['issues'])
    assert any(x['rule']=='personal-windows-path' for x in result['issues'])


def test_release_scan_excludes_no_matching_value_from_output(tmp_path):
    token='ghp_'+'A'*36
    (tmp_path/'sample.txt').write_text(token,encoding='utf-8')
    result=scanner.scan(tmp_path,strict=True)
    assert any(x['rule']=='github-token' for x in result['issues'])
    assert token not in str(result)


def test_release_scan_accepts_source_with_synthetic_cookie_names(tmp_path):
    (tmp_path/'sample.py').write_text("cookies = {'token':'SYNTHETIC', 'userid':'42'}\n",encoding='utf-8')
    assert scanner.scan(tmp_path,strict=True)['clean']
