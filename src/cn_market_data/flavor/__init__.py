"""Flavor (味道数据) subpackage: unified schema + dataset registry."""

from cn_market_data.flavor.schema import (
    DATASETS,
    FLAVOR_CORE_COLUMNS,
    FLAVOR_OPTIONAL_COLUMNS,
    dataset_to_path,
)

__all__ = [
    "DATASETS",
    "FLAVOR_CORE_COLUMNS",
    "FLAVOR_OPTIONAL_COLUMNS",
    "dataset_to_path",
]
