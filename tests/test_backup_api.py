"""Local API alternatives are opt-in and independent, not hidden credential relays."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from musicbox import backup_api
from musicbox import app as routes
from musicbox.models import StreamInfo


@pytest.mark.parametrize('url', ['https://example.com:3000','http://localhost:3000','http://127.0.0.1.attacker.com:3000','http://user:secret@127.0.0.1:3000','file:///tmp/a','http://127.0.0.1'])
def test_remote_or_ambiguous_api_address_rejected(url):
    with pytest.raises(ValueError): backup_api.validate(url,'/song/url')


def test_each_provider_choice_saved_independently(temp_home):
    backup_api.save('qq','official','','/music/url')
    backup_api.save('kugou','backup','http://127.0.0.1:3000','/song/url')
    assert backup_api.get('qq')['mode']=='official'
    assert backup_api.get('kuwo')['mode']=='official'
    assert backup_api.get('kugou')['mode']=='backup'
    backup_api.save('kugou','official','http://127.0.0.1:3000','/song/url')
    assert backup_api.resolve('kugou','a','128') is None


def test_backup_request_has_no_account_cookie(temp_home):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            body=json.dumps({'url':['https://cdn.example/allowed.mp3']}).encode()
            self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        routes.get_provider('kugou').apply_cookie('token=SECRET; userid=42')
        backup_api.save('kugou','backup',f'http://127.0.0.1:{server.server_port}','/song/url')
        info=backup_api.resolve('kugou','hash','128')
        assert info.url=='https://cdn.example/allowed.mp3'
        assert len(seen)==1 and not seen[0].get('Cookie') and not seen[0].get('Authorization')
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_default_official_never_calls_backup(temp_home,monkeypatch):
    provider=routes.get_provider('qq')
    expected=StreamInfo(url='https://cdn.example/a.mp3',quality='128')
    monkeypatch.setattr(provider,'stream',lambda *a,**k:expected)
    assert routes._resolve_playback(provider,'mid','128') is expected


def test_http_source_settings_and_failure_no_silent_fallback(temp_home,monkeypatch):
    with TestClient(routes.app) as c:
        assert c.post('/api/playback-source/kuwo',json={'mode':'backup','baseUrl':'https://evil.example:443','path':'/x'}).status_code==400
        assert c.get('/api/playback-source/qq').json()['mode']=='official'
        assert c.post('/api/playback-source/kuwo',json={'mode':'backup','baseUrl':'http://127.0.0.1:9','path':'/kuwo/url'}).status_code==200
        monkeypatch.setattr(routes.get_provider('kuwo'),'stream',lambda *a,**k:pytest.fail('Must not silently use official when user selected backup'))
        response=c.get('/api/stream/kuwo/1?quality=128')
        assert response.status_code==424
        assert '备用' in response.json()['detail']
