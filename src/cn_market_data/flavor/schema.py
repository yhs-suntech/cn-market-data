"""味道数据统一 schema (L2 flavor contract).

Design: each data-set = 1 typed dataclass + 1 DataFrame column contract;
organized by ``(symbol, date)``; warehouse partitions per dataset.

Fields are frozen per-dataset once the actual a-stock-data endpoint responses
are confirmed (see a-stock-data ``references/layerNN-*.md``).  The registry
below is the single source of truth — 4 original duplicated sources
(PanWatch vendors / InStock 14 crawlers / a-stock-data endpoints) must no
longer expose their own取数 paths after P2.

This module is part of the P1 contract gate: the 4 duplicated sources are
recorded here as their canonical dataset identities, not as standalone loaders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Unified DataFrame column contract (L2 flavor)
# ---------------------------------------------------------------------------
# Every flavor DataFrame is organized as (symbol, date) keys, with these
# core columns (required) + optional enrichment columns (per dataset).
FLAVOR_CORE_COLUMNS: List[str] = [
    "code",          # A-share code (e.g. "000001")
    "date",          # YYYY-MM-DD
]
FLAVOR_OPTIONAL_COLUMNS: List[str] = [
    "volume", "amount", "vwap", "price", "change", "change_pct",
]

# ---------------------------------------------------------------------------
# Unified dataset registry (symbol -> dataset identity)
# Each dataset maps to one parquet partition under warehouse/flavor/.
# ---------------------------------------------------------------------------
DATASETS: Dict[str, str] = {
    # 龙虎榜
    "lhb": "龙虎榜",
    # 资金流
    "money_flow": "资金流",
    # 大宗交易
    "dzjy": "大宗交易",
    # 分红
    "fhps": "分红",
    # 融资融券
    "rzrq": "融资融券",
    # 股东户数
    "gdfx": "股东户数",
    # 财报/F10
    "f10": "财报F10",
    # 涨停池
    "zttp": "涨停池",
    # 新闻/快讯
    "news": "新闻快讯",
    # 北向资金
    "northbound": "北向资金",
    # 宏观利率
    "macro": "宏观利率",
    # 指数成分/权重/估值
    "index_valuation": "指数成分估值",
    # 事件驱动
    "event": "事件驱动",
    # 可转债
    "cb": "可转债",
    # 期货/大宗
    "futures": "期货大宗",
}


# ---------------------------------------------------------------------------
# Per-dataset typed dataclass stubs (frozen field tables filled at P1 per source)
# ---------------------------------------------------------------------------
@dataclass
class LHBOrderBook:
    """龙虎榜 — code, date, ranking_times, 买卖席位 TOP5."""
    code: str
    date: str
    ranking_times: int = 0
    top_buy_seats: List[str] = field(default_factory=list)
    top_sell_seats: List[str] = field(default_factory=list)


@dataclass
class MoneyFlow:
    """资金流 — 主力净额/占比、16档大小单."""
    code: str
    date: str
    main_net: float = 0.0
    main_net_pct: float = 0.0
    big_order_flow: List[float] = field(default_factory=list)
    small_order_flow: List[float] = field(default_factory=list)


@dataclass
class FundFlowRZRQ:
    """融资融券 — 融资余额/融券余额."""
    code: str
    date: str
    margin_balance: float = 0.0
    short_balance: float = 0.0


@dataclass
class GDFX:
    """股东户数 — 户数/变化/均持股."""
    code: str
    date: str
    account_count: int = 0
    change: int = 0
    avg_holding: float = 0.0


@dataclass
class F10:
    """财报/F10 — 收入/净利/毛利率/ROE/资产负债."""
    code: str
    date: str
    revenue: float = 0.0
    net_profit: float = 0.0
    gross_margin: float = 0.0
    roe: float = 0.0
    balance_items: Dict[str, float] = field(default_factory=dict)


@dataclass
class LimitPool:
    """涨停池 — 涨停/炸板/跌停家数、涨停原因."""
    code: str
    date: str
    limit_up: int = 0
    break_board: int = 0
    limit_down: int = 0
    reason: str = ""


@dataclass
class News:
    """新闻/快讯 — title/content/time."""
    code: str
    date: str
    title: str = ""
    content: str = ""
    time: str = ""


@dataclass
class Northbound:
    """北向资金 — 净买入/累计."""
    date: str
    net_buy: float = 0.0
    cumulative_buy: float = 0.0


def dataset_to_path(dataset: str) -> str:
    """Dataset -> warehouse flavor partition path (relative to warehouse root)."""
    return f"flavor/{DATASETS[dataset]}/YYYY-MM-DD/"
