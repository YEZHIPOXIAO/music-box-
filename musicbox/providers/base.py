"""Provider contract."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from ..http import HttpClient
from ..models import Lyrics, StreamInfo, Track


class ProviderError(RuntimeError):
    """Raised when a provider cannot answer (network, rights, region, ...)."""


class NotSupported(ProviderError):
    """The provider has no implementation for this operation."""


@dataclass
class ProviderInfo:
    id: str
    name: str
    color: str
    homepage: str
    qualities: tuple[str, ...]
    supports_login: bool
    login_methods: tuple[str, ...]
    note: str = ""


class Provider(abc.ABC):
    id: str = ""
    name: str = ""
    color: str = "#888888"
    homepage: str = ""
    qualities: tuple[str, ...] = ("128",)
    login_methods: tuple[str, ...] = ("cookie",)

    def __init__(self) -> None:
        from ..credentials import POLICIES
        policy = POLICIES[self.id]
        self.http = HttpClient(cookie_domain="." + policy["domain"], credential_hosts=policy["hosts"])

    # -- identity --------------------------------------------------------
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=self.id,
            name=self.name,
            color=self.color,
            homepage=self.homepage,
            qualities=self.qualities,
            supports_login=bool(self.login_methods),
            login_methods=self.login_methods,
            note=self.login_note(),
        )

    def login_note(self) -> str:
        return ""

    # -- account ---------------------------------------------------------
    def apply_cookie(self, cookie_string: str) -> dict[str, str]:
        from ..credentials import POLICIES, parse_credentials, credential_values
        records = parse_credentials(self.id, cookie_string)
        values = credential_values(records, "www." + POLICIES[self.id]["domain"] if self.id != "qq" else "u.y.qq.com")
        if not values:
            return {}
        self.http.set_cookie_records(records)
        return values

    def validate_cookie(self, cookie_string: str) -> str:
        """Validate BEFORE mutating the current account; preserve domain metadata."""
        from ..credentials import POLICIES, parse_credentials, credential_values, has_credentials, dump_credentials
        records = parse_credentials(self.id, cookie_string)
        host = "u.y.qq.com" if self.id == "qq" else "www." + POLICIES[self.id]["domain"]
        if not has_credentials(self.id, credential_values(records, host)):
            raise ValueError(f"未检测到 {self.name} 的完整登录凭证，请在官方页面完成登录")
        return dump_credentials(records)

    def clear_account(self) -> None:
        self.http.clear_cookies()

    def account_state(self) -> dict[str, Any]:
        from ..credentials import has_credentials
        cookies = self.http.cookies("u.y.qq.com" if self.id == "qq" else "www." + ("kugou.com" if self.id == "kugou" else "kuwo.cn"))
        return {
            "provider": self.id,
            "loggedIn": has_credentials(self.id, cookies),
            "cookieNames": sorted(cookies.keys()),
            "cookieCount": len(cookies),
        }

    def check_membership(self) -> dict[str, Any]:
        return {"provider": self.id, "credentialPresent": self.account_state()["loggedIn"],
                "identityVerified": False, "membershipVerified": False, "isVip": None,
                "listeningTimeVerified": False, "remainingListeningSeconds": None,
                "playbackEligibility": "per-track-platform-decision",
                "message": "尚未向平台验证会员"}

    # -- music -----------------------------------------------------------
    @abc.abstractmethod
    def search(self, keyword: str, page: int = 1, limit: int = 20) -> list[Track]:
        ...

    @abc.abstractmethod
    def stream(self, track_id: str, quality: str = "auto", extra: dict | None = None) -> StreamInfo:
        ...

    @abc.abstractmethod
    def lyrics(self, track_id: str, extra: dict | None = None) -> Lyrics:
        ...

    def playlist(self, playlist_id: str) -> dict[str, Any]:
        raise NotSupported(f"{self.name} 暂不支持导入歌单")
