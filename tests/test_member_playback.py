"""Web-account entitlement request regressions. Synthetic credentials only."""
import json
import pytest
from fastapi.testclient import TestClient
from musicbox.providers.kugou import KugouProvider
from musicbox.providers.kuwo import KuwoProvider
from musicbox.providers.base import ProviderError
from musicbox.web_auth import kugou_params, kuwo_secret, KUWO_SEED_COOKIE
from musicbox import app as routes


def test_cookie_path_scoping_does_not_turn_server_refresh_into_account_conflict():
    from musicbox.credentials import credential_values
    records=[{'name':'websid','value':'ROOT','domain':'.kuwo.cn','path':'/'},
             {'name':'websid','value':'OTHER_PATH','domain':'.kuwo.cn','path':'/vip'}]
    assert credential_values(records,'www.kuwo.cn')['websid']=='ROOT'
    assert credential_values(records,'www.kuwo.cn','/vip/v2/check')['websid']=='OTHER_PATH'
    assert credential_values(records,'www.kuwo.cn','/viper')['websid']=='ROOT'


def test_server_cookie_refresh_replaces_dot_domain_alias():
    import requests
    p=KuwoProvider();p.apply_cookie(f'userid=42; websid=SESSION; {KUWO_SEED_COOKIE}=OLD')
    p.http.session.cookies.set(KUWO_SEED_COOKIE,'OLD_HOST',domain='.www.kuwo.cn',path='/')
    fresh=requests.cookies.create_cookie(KUWO_SEED_COOKIE,'NEW',domain='www.kuwo.cn',path='/')
    fresh.domain_specified=False
    p.http.session.cookies.set_cookie(fresh)
    values=p.http.cookies('www.kuwo.cn')
    assert values[KUWO_SEED_COOKIE]=='NEW'
    aliases=[c for c in p.http.session.cookies if c.name==KUWO_SEED_COOKIE and c.domain.lstrip('.')=='www.kuwo.cn']
    assert len(aliases)==1 and not aliases[0].domain_specified


def test_kugou_current_deluxe_role_verified_only_with_server_success(monkeypatch):
    p=KugouProvider();p.apply_cookie('KuGoo=KugooID=42&t=SESSION&a_id=1014')
    monkeypatch.setattr(p.http,'get_json',lambda *a,**k:{'errno':0,'error_code':0,'role':11,'isExpiredMember':0})
    state=p.check_membership()
    assert state['identityVerified'] and state['membershipVerified'] and state['isVip']
    assert state['membershipType']=='豪华VIP'
    monkeypatch.setattr(p.http,'get_json',lambda *a,**k:{'errno':105,'error_code':20017})
    assert p.check_membership()['isVip'] is None


def test_paid_badge_never_blocks_platform_free_time_authorization(monkeypatch):
    import musicbox.providers.kugou as module
    p=KugouProvider()
    calls=[]
    def play(song_hash, album_id=''):
        calls.append(song_hash)
        return {'url':'https://cdn.example/full.mp3','fileSize':2880000,'bitRate':128}
    monkeypatch.setattr(p,'_play_info',play)
    monkeypatch.setattr(module,'probe_stream',lambda *a,**k:{'format':'mp3','bitrate':128,'duration_matches':True})
    assert p.stream('HASH','128',{'paid':True,'duration':180}).url.endswith('full.mp3')
    assert calls==['HASH']


def test_inactive_membership_does_not_block_account_playback(monkeypatch):
    import musicbox.providers.kuwo as module
    monkeypatch.setattr(__import__('musicbox.web_auth',fromlist=['kuwo_secret']),'kuwo_secret',lambda *a,**k:'SYNTHETIC_SECRET')
    p=KuwoProvider();p.apply_cookie(f'userid=42; websid=SESSION; {KUWO_SEED_COOKIE}=SEED')
    monkeypatch.setattr(p,'check_membership',lambda:pytest.fail('Membership status must not gate playing'))
    monkeypatch.setattr(p.http,'get_json',lambda *a,**k:{'code':200,'data':{'url':'https://cdn.example/full.mp3'}})
    monkeypatch.setattr(module,'probe_stream',lambda *a,**k:{'format':'mp3','bitrate':128,'duration_matches':True})
    assert p.stream('123','128').url.endswith('full.mp3')
    assert p.account_state()['readyForVip'] is False


def test_membership_does_not_invent_free_minutes(monkeypatch):
    p=KuwoProvider();p.apply_cookie('userid=42; websid=SESSION')
    monkeypatch.setattr(p.http,'post',lambda *a,**k:Response({'meta':{'code':200},'data':{'vipLuxuryExpire':0,'vipmExpire':0},'ctime':200}))
    state=p.check_membership()
    assert state['isVip'] is False
    assert state['remainingListeningSeconds'] is None and not state['listeningTimeVerified']
    assert state['playbackEligibility']=='per-track-platform-decision'


def test_range_resolution_reuses_short_authorization_but_rebind_invalidates(monkeypatch,temp_home):
    from musicbox.models import StreamInfo
    p=routes.get_provider('kugou');p.apply_cookie('userid=42; token=ONE')
    routes._stream_cache.clear();calls=[]
    def play(*a,**k):
        calls.append(1);return StreamInfo('https://cdn.example/audio.mp3','128')
    monkeypatch.setattr(p,'stream',play)
    first=routes._resolve_playback(p,'HASH','128',{'duration':180})
    assert routes._resolve_playback(p,'HASH','128',{'duration':180}) is first and len(calls)==1
    p.apply_cookie('userid=43; token=TWO')
    assert routes._resolve_playback(p,'HASH','128',{'duration':180}) is not first and len(calls)==2
    for key in list(routes._stream_cache):
        if key[0]=='resolved': routes._stream_cache[key]=(0,routes._stream_cache[key][1])
    routes._resolve_playback(p,'HASH','128',{'duration':180});assert len(calls)==3
    routes._stream_cache.clear()


def test_missing_unlicensed_adapter_fails_explicitly(monkeypatch):
    monkeypatch.delenv('MUSICBOX_WEB_AUTH_MODULE', raising=False)
    with pytest.raises(ProviderError, match='开源包'):
        kuwo_secret('SYNTHETIC')
    with pytest.raises(ProviderError, match='开源包'):
        kugou_params({}, {'hash':'HASH'})


def test_explicit_local_adapter_contract(monkeypatch):
    import sys, types
    module=types.ModuleType('musicbox_synthetic_adapter')
    module.kugou_params=lambda values,fields,now_ms=None:{**fields,'userid':values.get('KugooID'), 'token':values.get('t'),'signature':'SYNTHETIC'}
    module.kuwo_secret=lambda value,seed=None:'SYNTHETIC_SECRET'
    monkeypatch.setitem(sys.modules,'musicbox_synthetic_adapter',module)
    monkeypatch.setenv('MUSICBOX_WEB_AUTH_MODULE','musicbox_synthetic_adapter')
    assert kuwo_secret('SYNTHETIC')=='SYNTHETIC_SECRET'
    assert kugou_params({'KugooID':'42','t':'TOKEN'},{'hash':'HASH'})['userid']=='42'


def test_kugou_uses_web_endpoint_with_bound_web_identity(monkeypatch):
    monkeypatch.setattr(__import__('musicbox.web_auth',fromlist=['kugou_params']),'kugou_params',lambda values,fields:{**fields,'token':values.get('t'),'userid':values.get('KugooID'),'signature':'SYNTHETIC'})
    p=KugouProvider();p.apply_cookie('KuGoo=KugooID=42&t=WEB_TOKEN&a_id=1014')
    seen={}
    def get(url,**kwargs):
        seen.update(url=url,**kwargs)
        return {'status':1,'data':{'play_url':'https://cdn.example/ok.mp3','is_free_part':0}}
    monkeypatch.setattr(p.http,'get_json',get)
    result=p._play_info('HASH','987')
    assert seen['url']=='https://wwwapi.kugou.com/play/songinfo'
    assert seen['params']['token']=='WEB_TOKEN' and seen['params']['userid']=='42'
    assert seen['params']['album_id']=='987' and 'signature' in seen['params']
    assert result['url'].endswith('ok.mp3')


def test_kugou_never_accepts_vip_preview_as_full_song(monkeypatch):
    monkeypatch.setattr(__import__('musicbox.web_auth',fromlist=['kugou_params']),'kugou_params',lambda values,fields:fields)
    p=KugouProvider();p.apply_cookie('userid=42; token=WEB_TOKEN')
    monkeypatch.setattr(p.http,'get_json',lambda *a,**k:{'status':1,'data':{'play_url':'https://cdn.example/preview.mp3','is_free_part':1}})
    with pytest.raises(ProviderError,match='试听'):p._play_info('HASH')


def test_kuwo_bound_account_uses_official_web_and_secret(monkeypatch):
    import musicbox.providers.kuwo as module
    monkeypatch.setattr(__import__('musicbox.web_auth',fromlist=['kuwo_secret']),'kuwo_secret',lambda *a,**k:'SYNTHETIC_SECRET')
    p=KuwoProvider();p.apply_cookie(f'userid=42; websid=SESSION; {KUWO_SEED_COOKIE}=SYNTHETIC')
    seen=[]
    def get(url,**kwargs):
        seen.append((url,kwargs));return {'code':200,'data':{'url':'https://cdn.example/ok.mp3'}}
    monkeypatch.setattr(p.http,'get_json',get)
    monkeypatch.setattr(p.http,'get_text',lambda *a,**k:pytest.fail('Logged-in account must not use anonymous antiserver'))
    monkeypatch.setattr(module,'probe_stream',lambda *a,**k:{'error':None,'format':'mp3','duration_matches':True,'bitrate':128})
    info=p.stream('123','128',{'duration':180})
    assert info.url.endswith('ok.mp3')
    assert seen[0][0]=='https://www.kuwo.cn/api/v1/www/music/playUrl'
    assert seen[0][1]['params']['mid']=='123' and seen[0][1]['headers']['Secret']
    assert 'SESSION' not in json.dumps(seen)


def test_kuwo_old_binding_missing_seed_has_actionable_error():
    p=KuwoProvider();p.apply_cookie('userid=42; websid=SESSION')
    with pytest.raises(ProviderError,match='重新'):p.stream('123','128')


def test_kuwo_new_cookie_bundle_preserves_web_validation_field():
    p=KuwoProvider();bundle=p.validate_cookie(f'userid=42; websid=SESSION; {KUWO_SEED_COOKIE}=SYNTHETIC')
    p.apply_cookie(bundle)
    assert p.http.cookies('www.kuwo.cn')[KUWO_SEED_COOKIE]=='SYNTHETIC'


class Response:
    status_code=200
    def __init__(self,data):self.text=json.dumps(data)
    def __enter__(self):return self
    def __exit__(self,*a):pass


@pytest.mark.parametrize('code,fields,verified,vip',[(200,{'vipLuxuryExpire':300,'vipmExpire':0},True,True),(200,{'vipLuxuryExpire':0,'vipmExpire':0},True,False),(401,{},False,None),(200,{},False,None)])
def test_kuwo_membership_is_queried_not_inferred(monkeypatch,code,fields,verified,vip):
    p=KuwoProvider();p.apply_cookie('userid=42; websid=SESSION')
    def post(url,**kwargs):
        assert url=='https://vip1.kuwo.cn/vip/v2/user/vip'
        assert kwargs['data']=={'op':'ui','uid':'42','sid':'SESSION'}
        return Response({'meta':{'code':code},'data':fields,'ctime':200})
    monkeypatch.setattr(p.http,'post',post)
    state=p.check_membership()
    assert state['membershipVerified']==verified and state['isVip'] is vip
    assert 'SESSION' not in json.dumps(state)


def test_kugou_role_without_matching_identity_is_unknown(monkeypatch):
    p=KugouProvider();p.apply_cookie('userid=42; token=SESSION')
    monkeypatch.setattr(p.http,'get_json',lambda *a,**k:{'role':0})
    state=p.check_membership()
    assert state['isVip'] is None and not state['membershipVerified']


def test_verify_endpoint_exposes_no_secrets_and_reports_backup_source(monkeypatch,temp_home):
    p=routes.get_provider('kuwo')
    monkeypatch.setattr(p,'check_membership',lambda:{'provider':'kuwo','credentialPresent':True,'identityVerified':True,'membershipVerified':True,'isVip':True,'message':'verified'})
    from musicbox.backup_api import save
    save('kuwo','backup','http://127.0.0.1:3000','/kuwo/url')
    with TestClient(routes.app) as c:
        r=c.post('/api/account/kuwo/verify',json={})
    assert r.status_code==200 and r.json()['isVip'] is True
    assert r.json()['playbackSource']=='backup' and '独立' in r.json()['message']
