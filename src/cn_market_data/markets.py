"""Package-local symbol->market classification (切缝 ②, pure functions).

Copied verbatim from Vibe-Trading ``backtest.engines._market_hooks`` so
akshare_loader / other extractable loaders can classify symbols without
importing Vibe engines. The A-share subset keeps the full market-pattern table
as a single package source of truth.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# ── Symbol -> market classification tables (A-share subset, copied from Vibe) ──

_CN_FUTURES_PRODUCTS: Dict[str, str] = {
    "if", "ic", "ih", "im", "t", "tf", "ts", "tl", "au", "ag", "cu", "al",
    "zn", "pb", "ni", "sn", "ss", "rb", "hc", "i", "j", "jm", "sc", "fu",
    "lu", "bu", "nr", "c", "cs", "m", "y", "a", "p", "jd", "lh", "rr", "pg",
    "cf", "sr", "ta", "ma", "ap", "rm", "oi", "cj", "pk", "cy", "pp", "l",
    "v", "eg", "eb", "pf", "sa", "fg", "ur", "si", "lc",
}

_CN_FUTURES_MAIN_PATTERN = re.compile(
    r"^(?:{})0$".format("|".join(sorted(_CN_FUTURES_PRODUCTS, key=len, reverse=True))),
    re.I,
)

_MARKET_PATTERNS: List[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I), "a_share"),
    (re.compile(r"^(51|15|56)\d{4}\.(SZ|SH)$", re.I), "a_share"),
    (re.compile(r"^[A-Z0-9&\.-]+\.US$", re.I), "us_equity"),
    (re.compile(r"^\d{3,5}\.HK$", re.I), "hk_equity"),
    (re.compile(r"^[A-Z0-9&\.-]+\.(NS|BO)$", re.I), "india_equity"),
    (re.compile(r"^\d{6}\.(KS|KQ)$", re.I), "kr_equity"),
    (re.compile(r"^[A-Z0-9&\.-]+\.(TO|V)$", re.I), "ca_equity"),
    (re.compile(r"^[A-Z0-9&\.-]+\.BA$", re.I), "ar_equity"),
    (re.compile(r"^[A-Z0-9]+\.SA$", re.I), "br_equity"),
    (re.compile(r"^[A-Z0-9&\.-]+\.L$", re.I), "uk_equity"),
    (re.compile(r"^[A-Z0-9]+\.VN$", re.I), "vietnam_equity"),
    (re.compile(r"^(?=[A-Z0-9]*[A-Z])[A-Z0-9]+-USDT$", re.I), "crypto"),
    (re.compile(r"^(?=[A-Z0-9]*[A-Z])[A-Z0-9]+/USDT$", re.I), "crypto"),
    (re.compile(r"^[A-Z0-9]+-USDT-PERP$", re.I), "crypto"),
    (re.compile(r"^(?=[A-Z0-9]*[A-Z])[A-Z0-9]+-USD$", re.I), "crypto"),
    (re.compile(r"^(?!\d+(?:USDT|USDC|BUSD)$)[A-Z0-9]{2,}(?:USDT|USDC|BUSD)$", re.I), "crypto"),
    (re.compile(r"^(?=[A-Z0-9]*[A-Z])[A-Z0-9]+[-/](?:USDC|BUSD)$", re.I), "crypto"),
    (
        re.compile(
            r"^[A-Za-z]{1,2}\d{3,4}\.(ZCE|DCE|SHFE|INE|CFFEX|GFEX|SHF|CZC|CFX|GFE)$",
            re.I,
        ),
        "futures",
    ),
    (re.compile(r"^(?:[A-Z][A-Z0-9]{1,4}|[0-9][A-Z][A-Z0-9]{0,3})[FGHJKMNQUVXZ]\d{1,4}$", re.I), "futures"),
    (re.compile(r"^(?:[A-Z][A-Z0-9]{1,4}|[0-9][A-Z][A-Z0-9]{0,3})\d{4}$", re.I), "futures"),
    (re.compile(r"^(?:[A-Z][A-Z0-9]{0,4}|[0-9][A-Z][A-Z0-9]{0,3})\.(CME|CBOT|NYMEX|COMEX|ICE|EUREX)$", re.I), "futures"),
    (
        re.compile(
            r"^(?:[A-Z][A-Z0-9]{0,4}|[0-9][A-Z][A-Z0-9]{0,3})(?:[FGHJKMNQUVXZ]\d{1,4}|\d{4})\.(CME|CBOT|NYMEX|COMEX|ICE|EUREX)$",
            re.I,
        ),
        "futures",
    ),
    (_CN_FUTURES_MAIN_PATTERN, "futures"),
]

_CHINA_EXCHANGES = {"CFFEX", "SHFE", "DCE", "ZCE", "INE", "GFEX"}
_EXCHANGE_ALIASES = {"SHF": "SHFE", "CZC": "ZCE", "CFX": "CFFEX", "GFE": "GFEX"}


def strip_local_prefix(code: str) -> str:
    """Strip a ``local:`` prefix (port / internal symbol routing)."""
    return code.split(":", 1)[1] if code[:6].lower() == "local:" else code


def _detect_market(code: str) -> str:
    """Infer market type from symbol format; unknown defaults to ``a_share``."""
    return matched_market(code) or "a_share"


def matched_market(code: str) -> Optional[str]:
    symbol = strip_local_prefix(code)
    return next((m for p, m in _MARKET_PATTERNS if p.match(symbol)), None)


def _is_china_futures(code: str) -> bool:
    """Check whether a futures code belongs to a Chinese exchange.

    Recognises ``<product><delivery>.<exchange>`` and bare
    ``<product><delivery>`` (no exchange suffix) forms.
    """
    parts = strip_local_prefix(code).upper().split(".")
    if len(parts) == 2:
        return _EXCHANGE_ALIASES.get(parts[1], parts[1]) in _CHINA_EXCHANGES
    m = re.fullmatch(r"([A-Za-z]+)\d+", parts[0])
    if m:
        if m.group(1).lower() in _CN_FUTURES_PRODUCTS:
            return True
    return False


# Public alias (设计切缝② 契约: detect_market(code) -> market).
detect_market = _detect_market
