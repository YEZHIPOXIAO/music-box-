"""Provider registry."""

from __future__ import annotations

import threading
from typing import Iterable

from ..config import settings
from .base import NotSupported, Provider, ProviderError, ProviderInfo
from .kugou import KugouProvider
from .kuwo import KuwoProvider
from .qq import QQProvider

__all__ = [
    "Provider",
    "ProviderError",
    "NotSupported",
    "ProviderInfo",
    "registry",
    "get_provider",
    "all_infos",
]


class Registry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._lock = threading.Lock()

    def _build(self) -> dict[str, Provider]:
        built: dict[str, Provider] = {}
        for cls in (QQProvider, KugouProvider, KuwoProvider):
            # Skip before constructing so a disabled provider pays nothing.
            if cls.id in settings.disabled_providers:
                continue
            built[cls.id] = cls()
        return built

    def providers(self) -> dict[str, Provider]:
        if self._providers:
            return self._providers
        # Lazily built under a lock: two concurrent first requests must not
        # each create a full set, or one set's cookies would be orphaned.
        with self._lock:
            if not self._providers:
                self._providers = self._build()
        return self._providers

    def ids(self) -> list[str]:
        return list(self.providers().keys())

    def get(self, provider_id: str) -> Provider:
        provider = self.providers().get(provider_id)
        if provider is None:
            raise ProviderError(f"未知或不支持的音乐平台: {provider_id}")
        return provider

    def infos(self) -> Iterable[ProviderInfo]:
        for provider in self.providers().values():
            yield provider.info()


registry = Registry()


def get_provider(provider_id: str) -> Provider:
    return registry.get(provider_id)


def all_infos() -> list[ProviderInfo]:
    return list(registry.infos())
