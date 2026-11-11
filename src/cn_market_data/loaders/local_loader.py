"""Local data loader: reads TDX installed daily K-line cache (``vipdoc/lday``).

通达信安装目录下的 ``vipdoc/{sh,sz,bj}/lday/*.day`` 是标准日线缓存：每个文件对应一只股票，
每条记录 32 字节（小端），字段为::

    date   int32   YYYYMMDD（小端）
    open   int32   实际开盘价 × 100
    high   int32   实际最高价 × 100
    low    int32   实际最低价 × 100
    close  int32   实际收盘价 × 100
    amount float32  成交额（元）
    volume int32    成交量（股）
    reserved int32  保留字段（通常为 0）

价格字段存储为实际价 × 100，解析时 ÷100 还原为元。通达信默认不复权，故 ``adjustment="none"``。
本源仅作 A 股离线兜底：网络源（tencent/eastmoney/akshare 等）优先，本 loader 在离线时兜底，
cron 薄壳仍零网络依赖（直接读本地盘）。

目录路径由 ``TDX_LOCAL_VIPDOC`` 环境变量覆盖，默认 ``D:/Suntech/New_tdx/vipdoc``（本机通达信安装目录）。
符号可以是带市场前缀的 ``sh600030``/``sz000001``/``bj899050``，也可以是纯 6 位代码（按代码前缀自动推断 sh/sz/bj）。
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from cn_market_data.base import validate_date_range, validate_ohlc
from cn_market_data.registry import register

logger = logging.getLogger(__name__)

# 32 字节/记录：date, open, high, low, close(int×100), amount(float), volume, reserved —— 小端
_OHLCV_RECORD_FMT = "<IIIII fII"


def _default_vipdoc_root() -> Path:
    """Return the TDX vipdoc root: env override, else the default local install dir."""
    env = os.getenv("TDX_LOCAL_VIPDOC", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("D:/Suntech/New_tdx/vipdoc").expanduser()


def _infer_market(code: str) -> str:
    """Map a symbol to the market whose lday directory holds its ``.day`` file."""
    if code.startswith(("sh", "sz", "bj")):
        return code[:2]  # 带市场前缀的符号，返回完整市场名 sh/sz/bj
    if code.startswith(("60", "68")):
        return "sh"  # 沪市：60/68 开头（如 600406、688001）
    if code.startswith(("00", "30", "20", "15")):
        return "sz"  # 深市：00/30/20/15 开头（如 000001、300075）
    if code.startswith(("89", "43")):
        return "bj"  # 北交所：89/43 开头（如 899050）
    return "sz"  # 未知前缀按深市默认；读不到文件会跳过


def _market_day_dir(root: Path, market: str) -> Path:
    return root / market / "lday"


def _resolve_day_file(root: Path, code: str) -> Optional[Path]:
    market = _infer_market(code)
    day_dir = _market_day_dir(root, market)
    # 带前缀的符号直接用原名；纯代码按推断市场拼文件名
    if code.startswith(("sh", "sz", "bj")):
        candidates = [code]
    else:
        candidates = [f"{market}{code}"]
    for cand in candidates:
        path = day_dir / f"{cand}.day"
        if path.exists():
            return path
    return None


def _parse_day_file(path: Path) -> pd.DataFrame:
    """Parse one ``.day`` file into an OHLCV DataFrame (32-byte records)."""
    data = path.read_bytes()
    n = len(data) // 32
    records: List[Dict[str, Any]] = []
    for i in range(n):
        buf = data[i * 32:(i + 1) * 32]
        (
            date,
            open_,
            high,
            low,
            close,
            amount,
            volume,
            reserved,
        ) = struct.unpack(_OHLCV_RECORD_FMT, buf)
        records.append(
            {
                "trade_date": dt.datetime.strptime(str(date), "%Y%m%d"),
                "open": round(open_ / 100.0, 4),
                "high": round(high / 100.0, 4),
                "low": round(low / 100.0, 4),
                "close": round(close / 100.0, 4),
                "amount": float(amount),
                "volume": int(volume),
            }
        )
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


@register
class TdxLocalLoader:
    """A股离线兜底加载器：读通达信 vipdoc/lday 标准日线缓存。"""

    name = "local"
    markets = {"a_share", "bj_equity"}
    requires_auth = False

    def __init__(self) -> None:
        self._root = _default_vipdoc_root()

    def is_available(self) -> bool:
        """True when the TDX vipdoc root exists and holds at least one ``.day`` file."""
        if not self._root.exists():
            return False
        for market in ("sh", "sz", "bj"):
            day_dir = _market_day_dir(self._root, market)
            if not day_dir.exists():
                continue
            if any((day_dir / f"{c}.day").exists() for c in ("sh600001", "sz000001", "bj899050")):
                return True
        return False

    def fetch(
        self,
        codes: List[str],
        start_date: str,
        end_date: str,
        *_
        ,
        interval: str = "1D",
        fields: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch A-share daily bars (OHLCV) for each code from the local ``.day`` cache.

        Args:
            codes: Symbol list (``sh600030`` / ``600030`` / ``sz000001`` / ``300075``).
            start_date: YYYY-MM-DD (inclusive).
            end_date: YYYY-MM-DD (inclusive of the whole end day).
            interval: Always ``1D`` for this local source; returned unchanged.
            fields: Ignored — the source is a static local cache.

        Returns:
            Mapping ``{symbol: OHLCV DataFrame(index=trade_date, open/high/low/close/volume/amount)}``
            with attrs ``{quote_currency=CNY, currency_conversion=none, adjustment=none}``.
        """
        validate_date_range(start_date, end_date)
        start = pd.Timestamp(start_date).normalize()
        # half-open on the end day so the last full day is inclusive
        end_exclusive = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            path = _resolve_day_file(self._root, code)
            if path is None:
                logger.warning("local loader: no .day file for %s under %s", code, self._root)
                continue
            try:
                df = _parse_day_file(path)
            except Exception as exc:  # noqa: BLE001 - one bad file must not fail the batch
                logger.warning("local loader: parse failed for %s: %s", code, exc)
                continue
            df = df[(df["trade_date"] >= start) & (df["trade_date"] < end_exclusive)].copy()
            if df.empty:
                logger.warning("local loader: %s has no bars in [%s, %s)", code, start_date, end_date)
                continue
            df = df.set_index("trade_date").sort_index()
            df = validate_ohlc(df)
            df = df[["open", "high", "low", "close", "volume", "amount"]].astype(
                {
                    "open": "float64",
                    "high": "float64",
                    "low": "float64",
                    "close": "float64",
                    "volume": "int64",
                    "amount": "float64",
                }
            )
            df.index.name = "trade_date"
            df.attrs = {
                "quote_currency": "CNY",
                "currency_conversion": "none",
                "adjustment": "none",  # 通达信默认不复权
            }
            result[code] = df
        return result


# Kept as a re-export so the module signature matches the base loader's cache
# hook pattern; this local source reads a static disk cache directly (no network),
# so no runtime cache lookup is needed — the re-export is documentation-only.
__all__ = ["TdxLocalLoader"]
