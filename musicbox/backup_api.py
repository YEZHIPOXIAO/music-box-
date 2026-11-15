"""Independent, opt-in loopback playback API choices.

No account cookies are sent to backup services. Each service manages its own
login. Repo URLs are documentation, never executable service endpoints.
"""
from __future__ import annotations
import ipaddress
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

import requests
from .config import settings
from .models import StreamInfo

SOURCES = {
    'qq': {'repository': 'https://github.com/L-1124/QQMusicApi', 'path': '',
           'note': '这是 Python 库；需先运行你自己的本机 HTTP 桥接服务，配置路径与其一致。'},
    'kugou': {'repository': 'https://github.com/MakcRe/KuGouMusicApi', 'path': '/song/url',
              'note': '本机 KuGouMusicApi 的 /song/url；API 登录由该服务自己管理，网页凭证不会自动传过去。'},
    'kuwo': {'repository': 'https://github.com/qyhqiu/kuwoMusicApi', 'path': '/kuwo/url',
             'note': '本机 kuwoMusicApi 的 /kuwo/url；该项目路由不含登录接口，登录继续使用官方网页。'},
}
_lock = threading.RLock()


def validate(base_url: str, path: str) -> tuple[str, str]:
    u = urlsplit(base_url)
    try:
        loopback = ipaddress.ip_address(u.hostname or '').is_loopback
    except ValueError:
        loopback = False
    if not loopback or u.scheme not in ('http','https') or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('备用 API 只接受本机回环地址，例如 http://127.0.0.1:3000')
    if not u.port:
        raise ValueError('请填写本机 API 的端口')
    p = urlsplit(path)
    if not path.startswith('/') or path.startswith('//') or p.scheme or p.netloc or p.query or p.fragment or '..' in path or len(path)>160:
        raise ValueError('API 路径必须为本机服务的相对路径，不含查询参数')
    return base_url.rstrip('/'), path


def load() -> dict:
    file = settings.data_dir / 'playback-sources.json'
    with _lock:
        try:
            value = json.loads(file.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise ValueError('播放来源配置损坏')
        return value


def get(provider: str) -> dict:
    if provider not in SOURCES:
        raise ValueError('未知平台')
    value = load().get(provider, {})
    return {'provider': provider, 'mode': value.get('mode', 'official'),
            'baseUrl': value.get('baseUrl',''), 'path': value.get('path', SOURCES[provider]['path']), **SOURCES[provider],
            # Persisted path wins over default metadata.
            'path': value.get('path', SOURCES[provider]['path'])}


def save(provider: str, mode: str, base_url: str, path: str) -> dict:
    if provider not in SOURCES or mode not in ('official','backup'):
        raise ValueError('无效播放来源')
    if base_url:
        base_url, path = validate(base_url, path)
    elif mode == 'backup':
        raise ValueError('请先配置本机备用 API 地址')
    with _lock:
        value = load()
        value[provider] = {'mode':mode, 'baseUrl':base_url, 'path':path}
        settings.ensure_dirs()
        target = settings.data_dir / 'playback-sources.json'
        temporary = target.with_suffix('.new')
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,target)
    return get(provider)


def resolve(provider: str, track_id: str, quality: str, extra: dict | None = None) -> StreamInfo | None:
    choice = get(provider)
    if choice['mode'] != 'backup':
        return None
    base,path = validate(choice['baseUrl'],choice['path'])
    if provider == 'kugou':
        params = {'hash':track_id, 'quality':'128' if quality=='auto' else quality,
                  'album_id':(extra or {}).get('albumId','')}
    elif provider == 'kuwo':
        params = {'mid':track_id,'type':'music','br':'320kmp3' if quality=='320' else '128kmp3'}
    else:
        params = {'id':track_id,'songmid':track_id,'type':quality}
    # Fresh session, no proxy-env/Cookie/auth header inheritance. Never follow
    # a backup service redirect to an unrelated machine.
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(base+path,params=params,timeout=12,allow_redirects=False)
        if response.status_code != 200:
            raise ValueError('备用 API 未返回成功结果，请检查本机服务和配置路径')
        if len(response.content)>256*1024:
            raise ValueError('备用 API 返回过大')
        data = response.json()
    body = data.get('body', data) if isinstance(data,dict) else data
    url = ''
    for _ in range(5):
        if isinstance(body,str):
            url=body; break
        if isinstance(body,list):
            body=body[0] if body else {}; continue
        if not isinstance(body,dict): break
        candidate=body.get('play_url') or body.get('url') or body.get('src')
        if candidate:
            body=candidate; continue
        body=body.get('data',body.get('result',{}))
    u=urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
        raise ValueError('备用 API 没有返回可播放地址；不会自动回退到其他平台')
    return StreamInfo(url=url,quality='unknown',bitrate=0,ext='flac' if u.path.lower().endswith('.flac') else 'mp3')
