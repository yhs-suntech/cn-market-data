"""Package-level loader registry: A-share fallback chain (L1, sole network layer).

Extraction from Vibe-Trading ``backtest.loaders.registry``, A-share subset only:
- ``FALLBACK_CHAINS["a_share"]`` — mootdx retired 2026-10-08, stays registered
  for explicit ``source="mootdx"`` use only (not in default chain).
- A-share loader self-registration (via ``@register``).
- A-share adjustment mapping.
- ``resolve_loader`` / ``get_loader_cls_with_fallback`` — single-market chain,
  no cross-market fallback.

Self-register loaders lazily (import the A-share loader modules on first use),
silently skipping modules whose optional provider deps are missing.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Type

from cn_market_data.base import NoAvailableSourceError

logger = logging.getLogger(__name__)

LOADER_REGISTRY: dict[str, Type[Any]] = {}


def register(cls: Type[Any]) -> Type[Any]:
    """Class decorator: register a loader into the package registry."""
    LOADER_REGISTRY[cls.name] = cls
    return cls


# A-share fallback chain: tencent -> eastmoney -> baostock -> akshare -> tushare -> local
FALLBACK_CHAINS: dict[str, list[str]] = {
    "a_share": [
        "tencent",
        "eastmoney",
        "baostock",
        "akshare",
        "tushare",
        "local",
    ],
}


def _ensure_registered() -> None:
    """Import the A-share loader modules so @register decorators fire."""
    modules = [
        "cn_market_data.loaders.tencent_loader",
        "cn_market_data.loaders.eastmoney_loader",
        "cn_market_data.loaders.akshare_loader",
        "cn_market_data.loaders.baostock_loader",
        "cn_market_data.loaders.mootdx_loader",
        "cn_market_data.loaders.tushare",
        "cn_market_data.loaders.local_loader",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
        except Exception as exc:  # optional provider missing -> skip silently
            logger.debug("skip loader module %s (deps missing): %s", m, exc)


# Import-time registration pass.
_ensure_registered()


def resolve_loader(market: str) -> Any:
    """Return the first *available* loader instance for *market* (single-market chain)."""
    _ensure_registered()
    chain = FALLBACK_CHAINS.get(market, [])
    tried: list[str] = []
    for name in chain:
        if name not in LOADER_REGISTRY:
            continue
        tried.append(name)
        try:
            loader = LOADER_REGISTRY[name]()
        except Exception as exc:
            logger.debug("loader %s failed to construct: %s", name, exc)
            continue
        if loader.is_available():
            return loader
    raise NoAvailableSourceError(
        f"No available data source for market '{market}'. "
        f"Tried: {tried or chain}. Check network and API token config."
    )


def get_loader_cls_with_fallback(source: str) -> Type[Any]:
    """Return a loader *class* for *source*, failing loudly if unavailable.

    ``local`` is the canonical no-fallback case (broad markets set exists only
    to reach cross-market resolvers) — never silently degrade.
    """
    _ensure_registered()
    if source not in LOADER_REGISTRY:
        raise NoAvailableSourceError(f"Unknown data source: {source}")
    loader_cls = LOADER_REGISTRY[source]
    try:
        instance = loader_cls()
    except Exception as exc:
        logger.debug("loader %s failed to construct: %s", source, exc)
        instance = None
    if instance is not None and instance.is_available():
        return loader_cls
    if source in _NO_NETWORK_FALLBACK_SOURCES:
        raise NoAvailableSourceError(
            f"Data source '{source}' is unavailable and does not fall back."
        )
    raise NoAvailableSourceError(
        f"Data source '{source}' is unavailable and no fallback found."
    )


# Sources that must never silently degrade to a network loader.
_NO_NETWORK_FALLBACK_SOURCES = frozenset({"local"})


# A-share adjustment mapping (复权口径). A-share subset of Vibe registry mapping.
ADJUSTMENT_MAP: dict[str, str] = {
    "eastmoney": "split_divident",
    "tencent": "split_divident_additive",
    "akshare": "split_divident",
    "baostock": "split_divident",
    "tushare": "split_divident",
}


def a_share_adjustment(source: str) -> str:
    """Return the adjustment口径 (复权) for an A-share loader source."""
    return ADJUSTMENT_MAP.get(source, "unknown")
