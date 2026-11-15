"""Optional, explicitly installed web-auth implementation boundary.

The private development build used routines ported from official web assets
whose redistribution license was not established. Those implementations are
NOT part of this MIT source export. See docs/PROVENANCE.md.

A maintainer may install a separately licensed Python module exposing
kugou_params(values, fields, now_ms=None) and kuwo_secret(value, seed=None), then
explicitly select its module name via MUSICBOX_WEB_AUTH_MODULE. Loading a Python
module executes local code; only select one you trust and have rights to use.
No module, key, account data or remote script is downloaded automatically.
"""
from __future__ import annotations
import importlib
import os
from .providers.base import ProviderError

# Public website Cookie field identifier, not a user Cookie value or secret.
KUWO_SEED_COOKIE = 'Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324'


def _implementation():
    module = os.environ.get('MUSICBOX_WEB_AUTH_MODULE', '').strip()
    if not module or module == __name__:
        raise ProviderError('此开源包未附带许可未核实的网页签名实现；请配置你有权使用的本机适配模块，或使用独立备用 API')
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ProviderError('已配置的本机网页签名模块无法加载；请检查安装和许可') from exc


def kugou_params(values: dict, fields: dict, now_ms: int | None = None) -> dict:
    implementation = _implementation()
    result = implementation.kugou_params(dict(values), dict(fields), now_ms=now_ms)
    if not isinstance(result, dict):
        raise ProviderError('本机网页签名模块返回格式无效')
    return result


def kuwo_secret(value: str, seed: int | None = None) -> str:
    result = _implementation().kuwo_secret(value, seed=seed)
    if not isinstance(result, str) or not result:
        raise ProviderError('本机网页校验模块返回格式无效')
    return result
