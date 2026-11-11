"""cn-market-data: unified A-share data source package (L1 / L2 / L3).

Independent package — no Vibe-Trading internal imports (切缝 ①②③ done).
- L1 源访问 (sole network layer): registry + loaders (A-share subset).
- L2 数据合约: OHLCV contract (dict[sym, DataFrame] + attrs) + flavor schema.
- L3 共享仓: post-sync Parquet warehouse, read-only by all projects + cron.

See docs/unified-a-share-data-design.md for the full design baseline.
"""

from cn_market_data.base import (
    DataLoaderProtocol,
    NoAvailableSourceError,
    loader_cache_enabled,
    loader_cache_root,
)
from cn_market_data.registry import (
    ADJUSTMENT_MAP,
    FALLBACK_CHAINS,
    a_share_adjustment,
    get_loader_cls_with_fallback,
    resolve_loader,
)

__all__ = [
    "DataLoaderProtocol",
    "NoAvailableSourceError",
    "loader_cache_enabled",
    "loader_cache_root",
    "resolve_loader",
    "get_loader_cls_with_fallback",
    "FALLBACK_CHAINS",
    "ADJUSTMENT_MAP",
    "a_share_adjustment",
]
