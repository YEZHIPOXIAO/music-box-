"""Login contract acceptance. Synthetic secrets only; no real account required."""
import json
import threading
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict
from fastapi.testclient import TestClient

from musicbox.http import HttpClient
from musicbox.login import QQLogin, QrSession
from musicbox.credentials import parse_credentials, dump_credentials, credential_values
from musicbox.providers.qq import QQProvider
from musicbox.providers.kugou import KugouProvider
from musicbox import app as routes


class WireAdapter(BaseAdapter):
    def __init__(self):
        self.seen = []

    def send(self, request, **kwargs):
        self.seen.append((request.url, dict(request.headers)))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response._content = b"OK"
        response._content_consumed = True
        response.headers = CaseInsensitiveDict()
        if request.url.endswith('/redirect'):
            response.status_code = 302
            response.headers['Location'] = 'http://u.y.qq.com/final'
        elif request.url.endswith('/foreign'):
            response.status_code = 302
            response.headers['Location'] = 'https://evil.example/final'
        return response

    def close(self):
        pass


@pytest.mark.parametrize('verb', ['get', 'stream'])
def test_cookie_scope_checked_at_every_redirect_send(verb):
    provider = QQProvider()
    provider.apply_cookie('uin=42; qm_keyst=SECRET_KEY')
    adapter = WireAdapter()
    provider.http.session.mount('https://', adapter)
    provider.http.session.mount('http://', adapter)
    getattr(provider.http, verb)('https://u.y.qq.com/redirect').close()
    assert len(adapter.seen) == 2
    assert 'SECRET_KEY' in adapter.seen[0][1].get('Cookie', '')
    assert 'SECRET_KEY' not in adapter.seen[1][1].get('Cookie', '')


def test_https_foreign_redirect_and_cdn_get_no_account_cookie():
    provider = QQProvider()
    provider.apply_cookie('uin=42; qm_keyst=SECRET_KEY')
    adapter = WireAdapter()
    provider.http.session.mount('https://', adapter)
    provider.http.get('https://u.y.qq.com/foreign').close()
    provider.http.stream('https://aqqmusic.tc.qq.com/file.mp3').close()
    assert 'SECRET_KEY' in adapter.seen[0][1].get('Cookie', '')
    assert all('SECRET_KEY' not in headers.get('Cookie', '') for _, headers in adapter.seen[1:])


def test_scope_survives_persistence_and_duplicate_uin_resolves_specific_domain():
    records = [
        {'name': 'uin', 'value': '42', 'domain': '.qq.com', 'path': '/'},
        {'name': 'uin', 'value': 'WRONG', 'domain': 'ptlogin2.qq.com', 'path': '/'},
        {'name': 'qm_keyst', 'value': 'MUSIC_KEY', 'domain': '.qq.com', 'path': '/'},
    ]
    bundle = dump_credentials(records)
    provider = QQProvider()
    provider.apply_cookie(provider.validate_cookie(bundle))
    assert provider._account() == {'uin': '42', 'key': 'MUSIC_KEY'}
    assert all(c.domain for c in provider.http.session.cookies)
    assert not provider.http.cookies('www.kugou.com')


def test_official_web_cookie_formats_are_recognized_without_promoting_token_kind():
    kg = KugouProvider()
    bundle = kg.validate_cookie('KuGoo=KugooID%3D777%26t%3DWEB_SESSION%26a_id%3D1014')
    kg.apply_cookie(bundle)
    assert kg.account_state()['loggedIn']
    assert kg.account_state()['credentialKind'] == 'web'
    assert not kg.http.cookies().get('token')
    from musicbox.providers.kuwo import KuwoProvider
    kw = KuwoProvider()
    kw.apply_cookie(kw.validate_cookie('userid=42; websid=WEB_SESSION; kw_token=CSRF'))
    assert kw.account_state()['loggedIn']
    assert not kw.account_state()['membershipVerified']


def test_rebinding_drops_old_account_but_preserves_scoped_device_ids():
    p = KugouProvider()
    p.apply_cookie(p.validate_cookie('userid=42; token=OLD'))
    p.apply_cookie(p.validate_cookie('KugooID=43; t=NEW_WEB'))
    values = p.http.cookies('www.kugou.com')
    assert 'token' not in values and values['KugooID'] == '43'
    assert 'kg_mid' in values
    p.clear_account()
    assert not p.account_state()['loggedIn']
    assert 'kg_mid' in p.http.cookies('www.kugou.com')


def test_qq_global_key_is_not_a_music_credential():
    p = QQProvider()
    with pytest.raises(ValueError):
        p.validate_cookie('uin=42; p_skey=GLOBAL_QQ_SECRET')


@pytest.mark.parametrize('provider,cookie', [('qq', 'qrsig=WAITING'), ('kugou', 'kg_mid=DEVICE'), ('kuwo', 'kw_token=PUBLIC_CSRF; userid=42')])
def test_anonymous_cookies_do_not_count_as_login(provider, cookie):
    with TestClient(routes.app) as client:
        response = client.post(f'/api/account/{provider}', json={'cookie': cookie})
        assert response.status_code == 400


def test_wrong_platform_and_oversized_cookie_rejected_without_mutating_account():
    p = routes.get_provider('qq')
    p.apply_cookie('uin=42; qm_keyst=OLD')
    with TestClient(routes.app) as client:
        assert client.post('/api/account/qq', json={'cookie': 'token=KG_TOKEN; userid=3'}).status_code == 400
        assert p._account()['key'] == 'OLD'
        assert client.post('/api/account/qq', json={'cookie': 'x' * 20000}).status_code == 422


class Response:
    def __init__(self, status=200, location=''):
        self.status_code = status
        self.headers = {'Location': location}
    def __enter__(self): return self
    def __exit__(self, *args): pass


class ExchangeHttp:
    def __init__(self, bad_state=False, rejected=False):
        self.bad_state = bad_state
        self.rejected = rejected
        self.cleared = False
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs))
        assert kwargs.get('allow_redirects') is False
        if '/check_sig' in url:
            return Response(302, 'https://graph.qq.com/oauth2.0/login_jump')
        return Response()
    def cookies(self, host=''):
        assert host == 'graph.qq.com'
        return {'p_uin': 'o42', 'p_skey': 'TEMP_QQ', 'pt_oauth_token': 'TEMP_OAUTH'}
    def post(self, url, **kwargs):
        self.calls.append(('POST', url, kwargs))
        state = 'attacker-state' if self.bad_state else kwargs['data']['state']
        return Response(302, 'https://y.qq.com/portal/wx_redirect.html?code=' + 'A'*32 + '&state=' + state)
    def post_json(self, url, payload, **kwargs):
        self.calls.append(('EXCHANGE', url, payload))
        assert payload['req']['module'] == 'QQConnectLogin.LoginServer'
        assert payload['req']['param']['code'] == 'A' * 32
        return {'code': 0, 'req': {'code': 1 if self.rejected else 0,
            'data': {'musicid': 42, 'musickey': 'REAL_MUSIC_KEY', 'loginType': 2}}}
    def clear_cookies(self): self.cleared = True
    def close(self): pass


@pytest.mark.parametrize('bad_state,rejected,expected', [(False, False, 'confirmed'), (True, False, 'failed'), (False, True, 'failed')])
def test_qq_identity_must_exchange_for_music_credential(bad_state, rejected, expected):
    flow = QQLogin()
    flow.http.close()
    fake = ExchangeHttp(bad_state, rejected)
    flow.http = fake
    state = QrSession('qq', 'session', b'PNG')
    flow._session = state
    flow._finish('https://ssl.ptlogin2.graph.qq.com/check_sig?ticket=TEMP', state)
    assert state.status == expected
    assert fake.cleared
    if expected == 'confirmed':
        bundle = flow.cookie_string()
        assert 'REAL_MUSIC_KEY' in bundle
        assert 'TEMP_QQ' not in bundle and 'TEMP_OAUTH' not in bundle
        p = QQProvider()
        p.apply_cookie(p.validate_cookie(bundle))
        assert p._account()['key'] == 'REAL_MUSIC_KEY'
    else:
        assert not flow.cookie_string()


def test_qq_refuses_untrusted_callback_before_any_request():
    flow = QQLogin()
    flow.http.close()
    fake = ExchangeHttp()
    flow.http = fake
    state = QrSession('qq', 's', b'PNG')
    flow._finish('https://evil.example/check_sig', state)
    assert state.status == 'failed' and not fake.calls


def test_parser_handles_quoted_commas_and_rejects_noncallback():
    flow = QQLogin()
    assert flow._parse("ptuiCB('66','0','','0','hello, world','');") == ('66', '')
    assert flow._parse('not a callback') == ('', '')
    flow.close()


def test_exchange_null_response_is_failed_not_unhandled_500():
    flow = QQLogin()
    flow.http.close()
    fake = ExchangeHttp()
    fake.post_json = lambda *a, **k: {'code': 0, 'req': {'code': 0, 'data': None}}
    flow.http = fake
    state = QrSession('qq', 's', b'PNG')
    flow._finish('https://ssl.ptlogin2.graph.qq.com/check_sig?ticket=TEMP', state)
    assert state.status == 'failed'
    assert '音乐凭证' in state.message


@pytest.mark.parametrize('error', [PermissionError('DENIED secret'), __import__('sqlite3').OperationalError('readonly database')])
def test_confirmed_qr_storage_error_is_actionable_not_http500(monkeypatch, error):
    import time
    class Flow:
        def __init__(self): self.lock = threading.Lock(); self.closed = False
        def poll(self): return QrSession('qq', 'save-error', b'PNG', status='confirmed')
        def cookie_string(self): return 'uin=42; qm_keyst=VALID'
        def close(self): self.closed = True
    flow = Flow()
    routes._qr_sessions['save-error'] = (flow, time.monotonic())
    monkeypatch.setattr(routes.store, 'save_account', lambda *a, **k: (_ for _ in ()).throw(error))
    with TestClient(routes.app, raise_server_exceptions=False) as client:
        response = client.get('/api/login/qq/poll?session=save-error')
    assert response.status_code == 503
    assert '保存' in response.json()['detail']
    assert 'secret' not in response.text
    assert flow.closed and 'save-error' not in routes._qr_sessions


def test_cross_site_qr_creation_blocked():
    with TestClient(routes.app) as client:
        response = client.get('/api/login/qq/qr', headers={'Sec-Fetch-Site': 'cross-site'})
        assert response.status_code == 403


def test_qr_get_does_not_create_sessions():
    with TestClient(routes.app) as client:
        assert client.get('/api/login/qq/qr').status_code == 405


def test_qr_capacity_and_expiry_close_owned_clients(monkeypatch):
    import time
    class Flow:
        def __init__(self): self.closed = False
        def close(self): self.closed = True
    old = dict(routes._qr_sessions)
    routes._qr_sessions.clear()
    try:
        flows = [Flow() for _ in range(routes._QR_MAX)]
        for index, flow in enumerate(flows):
            routes._qr_sessions[str(index)] = (flow, time.monotonic())
        with TestClient(routes.app) as client:
            assert client.post('/api/login/qq/qr').status_code == 429
        routes._qr_sessions['expired'] = (Flow(), time.monotonic() - routes._QR_TTL - 1)
        expired = routes._qr_sessions['expired'][0]
        with routes._qr_lock: routes._qr_sweep_locked()
        assert expired.closed and 'expired' not in routes._qr_sessions
    finally:
        routes._qr_sessions.clear()
        routes._qr_sessions.update(old)


def test_qr_cancel_releases_owned_http_session(monkeypatch):
    class Flow:
        def __init__(self):
            self.lock = threading.Lock()
            self.closed = False
        def close(self): self.closed = True
    flow = Flow()
    import time
    routes._qr_sessions['test-cancel'] = (flow, time.monotonic())
    with TestClient(routes.app) as client:
        response = client.delete('/api/login/qq/qr?session=test-cancel')
        assert response.json() == {'ok': True, 'removed': True}
        assert flow.closed and 'test-cancel' not in routes._qr_sessions
        assert not client.delete('/api/login/qq/qr?session=test-cancel').json()['removed']
