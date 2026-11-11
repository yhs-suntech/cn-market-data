"""Package-local config: env value access + loader cache switch/root.

This is the cut-scratch replacement for Vibe-Trading's ``src.config.accessor``
so the ``cn-market-data`` package can be ``pip install``-ed standalone
without importing any Vibe-Trading internal module (缝 ① & ③).

Key: only expose what the extracted base/registry need — env reading and the
two cache knobs (``vibe_trading_data_cache`` / ``vibe_trading_data_cache_root``)
read by ``cn_market_data.base``.  No other Vibe config surface is pulled in.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

# Shared D path so all 5 projects + all cron run against ONE warehouse copy.
# Override via VIBE_CN_MARKET_DATA_WAREHOUSE.
DEFAULT_WAREHOUSE_ROOT = Path(r"D:/cn-market-data/warehouse")


def get_env_value(key: str, default=None):
    """Read a single env var (package-local, no Vibe config import)."""
    return os.environ.get(key, default)


def get_env_config() -> SimpleNamespace:
    """Return a minimal config namespace the extracted base/registry consumes.

    ``data.vibe_trading_data_cache``  -> bool (loader cache enabled, opt-in)
    ``data.vibe_trading_data_cache_root`` -> str (cache root, default shared D path)
    """
    cache_enabled = os.environ.get(
        "VIBE_CN_MARKET_DATA_CACHE", "false"
    ).strip().lower() in ("1", "true", "yes")
    cache_root = os.environ.get(
        "VIBE_CN_MARKET_DATA_WAREHOUSE", str(DEFAULT_WAREHOUSE_ROOT)
    )
    return SimpleNamespace(
        data=SimpleNamespace(
            vibe_trading_data_cache=cache_enabled,
            vibe_trading_data_cache_root=cache_root,
        )
    )
