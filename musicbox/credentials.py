"""Validated, platform-scoped credentials (never browser-wide cookie dumps)."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote, parse_qs

POLICIES = {
    "qq": {
        "domain": "qq.com",
        "hosts": ("y.qq.com", "c.y.qq.com", "u.y.qq.com"),
        "names": {"uin", "qqmusic_uin", "wxuin", "qm_keyst", "qqmusic_key", "login_type"},
    },
    "kugou": {
        "domain": "kugou.com",
        "hosts": ("www.kugou.com", "songsearch.kugou.com", "m.kugou.com", "wwwapi.kugou.com", "login-user.kugou.com", "vip.kugou.com"),
        "names": {"token", "userid", "KugooID", "KuGoo", "t", "kg_mid", "kg_dfid", "kg_dfid_collect"},
    },
    "kuwo": {
        "domain": "kuwo.cn",
        "hosts": ("www.kuwo.cn", "m.kuwo.cn", "search.kuwo.cn", "antiserver.kuwo.cn", "nplserver.kuwo.cn", "vip1.kuwo.cn"),
        "names": {"userid", "uid", "sid", "websid", "web_sid", "token", "KwLoginToken", "kw_token", "Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324"},
    },
}
VALUE = re.compile(r"^[\x21-\x7e]{1,4096}$")


def belongs(domain: str, root: str) -> bool:
    domain = domain.lower().lstrip(".")
    return domain == root or domain.endswith("." + root)


def parse_credentials(provider: str, value: str) -> list[dict[str, Any]]:
    policy = POLICIES[provider]
    if value.lstrip().startswith("{"):
        data = json.loads(value)
        source = data.get("cookies", [])
        if not isinstance(source, list) or len(source) > 100:
            raise ValueError("Cookie 数据格式无效")
    else:
        source = []
        for part in value.split(";"):
            if "=" in part:
                name, val = part.strip().split("=", 1)
                source.append({"name": name.strip(), "value": val.strip(), "domain": policy["domain"], "path": "/"})
    result = []
    keys = set()
    for item in source:
        if not isinstance(item, dict):
            raise ValueError("Cookie 数据格式无效")
        name, val = item.get("name", ""), item.get("value", "")
        domain = item.get("domain", policy["domain"]).lower().lstrip(".")
        path = item.get("path", "/")
        if name not in policy["names"]:
            continue
        if not belongs(domain, policy["domain"]):
            raise ValueError("Cookie 域与所选音乐平台不匹配")
        if not isinstance(val, str) or not VALUE.fullmatch(val) or ";" in val:
            raise ValueError("Cookie 值格式无效")
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 256:
            raise ValueError("Cookie 路径无效")
        identity = (name, domain, path)
        if identity in keys:
            raise ValueError("Cookie 含重复身份字段")
        keys.add(identity)
        result.append({"name": name, "value": val, "domain": "." + domain, "path": path, "secure": True})
    return result


def credential_values(records: list[dict[str, Any]], host: str, request_path: str = "/") -> dict[str, str]:
    """Resolve duplicate names by most specific domain; ties fail closed."""
    values = {}
    specificity = {}
    for c in records:
        domain = c["domain"].lstrip(".")
        if not belongs(host, domain):
            continue
        path = c.get("path", "/")
        if not (request_path == path or request_path.startswith(path if path.endswith('/') else path + '/')):
            continue
        score = (len(domain), len(path))
        name = c["name"]
        if score == specificity.get(name) and values[name] != c["value"]:
            raise ValueError("登录 Cookie 同名凭证冲突，请重新登录")
        if score >= specificity.get(name, (-1, -1)):
            values[name] = c["value"]
            specificity[name] = score
    if values.get("KuGoo"):
        # Official desktop web login stores a_id/KugooID/t inside a URL-encoded
        # KuGoo cookie, rather than separate Android API token/userid cookies.
        # Preserve it as web provenance; do not pretend its t is an Android token.
        packed = parse_qs(unquote(values["KuGoo"]))
        if len(packed.get("KugooID", [])) == 1:
            values.setdefault("KugooID", packed["KugooID"][0])
        if len(packed.get("t", [])) == 1:
            values.setdefault("t", packed["t"][0])
    return values


def has_credentials(provider: str, values: dict[str, str]) -> bool:
    if provider == "qq":
        return bool((values.get("uin") or values.get("qqmusic_uin") or values.get("wxuin"))
                    and (values.get("qm_keyst") or values.get("qqmusic_key")))
    if provider == "kugou":
        return bool((values.get("userid") or values.get("KugooID")) and (values.get("token") or values.get("t")))
    # kw_token is a public CSRF value, NOT proof of a logged-in account.
    return bool((values.get("userid") or values.get("uid"))
                and (values.get("sid") or values.get("websid") or values.get("web_sid") or values.get("token") or values.get("KwLoginToken")))


def dump_credentials(records: list[dict[str, Any]]) -> str:
    return json.dumps({"version": 1, "cookies": records}, ensure_ascii=True, separators=(",", ":"))
