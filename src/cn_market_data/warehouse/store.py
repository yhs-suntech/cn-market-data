"""L3 shared warehouse: post-close Parquet store (pyarrow write/read, duckdb cross-query).

Layout (root, default ``D:/cn-market-data/warehouse``)::

    manifest.json                     # cache_version gate + dataset inventory
    daily/<sh|sz|bj>.parquet          # per-market daily-K OHLCV (code column)
    flavor/<dataset>/<YYYY-MM-DD>.parquet   # per-dataset cross-sections (code column)

Contract gates:
- ``cache_version`` — layout/unit/adjustment changes bump the version; readers
  holding an older manifest version raise :class:`CacheVersionMismatch` instead
  of silently mixing schemas (P1 gate ③).
- Flavor partitions follow :func:`cn_market_data.flavor.schema.dataset_to_path`.
- Read side is read-only; writes happen exclusively in the post-close sync job.

Heavy deps (pyarrow / duckdb) are lazy-imported so L1/L2 users without the
``warehouse`` extra never pay for them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

import pandas as pd

from cn_market_data.config import DEFAULT_WAREHOUSE_ROOT, get_env_config
from cn_market_data.flavor.schema import DATASETS, dataset_to_path

# Layout/units/adjustment version. Bump on any breaking layout change.
CACHE_VERSION = 9


class CacheVersionMismatch(Exception):
    """Manifest cache_version differs from what the reader expects."""


def warehouse_root() -> Path:
    """Resolve the L3 warehouse root (env override, else the shared D path)."""
    return Path(get_env_config().data.vibe_trading_data_cache_root)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _lazy_pa():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "L3 warehouse read/write needs pyarrow — install with "
            "`pip install 'cn-market-data[warehouse]'`"
        ) from exc
    return pa, pq


class L3Store:
    """Read/write access to the L3 shared warehouse (one process = one instance)."""

    def __init__(
        self,
        root: Optional[Union[str, Path]] = None,
        cache_version: int = CACHE_VERSION,
        read_only: bool = False,
    ) -> None:
        self.root = Path(root) if root is not None else warehouse_root()
        self.cache_version = int(cache_version)
        self.read_only = read_only
        self.manifest_path = self.root / "manifest.json"
        self.manifest: Dict[str, Any] = self._load_manifest()

    # ------------------------------------------------------------------ manifest

    def _load_manifest(self) -> Dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        with open(self.manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        stored = manifest.get("cache_version")
        if stored is not None and stored != self.cache_version:
            raise CacheVersionMismatch(
                f"manifest cache_version={stored} != reader cache_version="
                f"{self.cache_version}; the on-disk layout drifted — re-run the "
                "post-close sync job (never edit the warehouse by hand)."
            )
        return manifest

    def _save_manifest(self, manifest: Dict[str, Any]) -> None:
        if self.read_only:
            raise RuntimeError("L3Store is read-only; writes only in the sync job")
        manifest["cache_version"] = self.cache_version
        manifest["updated_at"] = _now_iso()
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        tmp.replace(self.manifest_path)

    def init_warehouse(self) -> None:
        """Create root dirs + a fresh manifest (first-time bootstrap only)."""
        if self.manifest_path.exists():
            raise RuntimeError(f"manifest already exists at {self.manifest_path}")
        self._save_manifest(
            {
                "root": str(self.root),
                "schema": "L2-OHLCV+flavor-v1",
                "created_at": _now_iso(),
                "datasets": {},
            }
        )
        for sub in ("daily", "flavor"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def refresh_manifest(self) -> Dict[str, Any]:
        """Re-scan the on-disk inventory and persist it into the manifest."""
        _pa, pq = _lazy_pa()
        manifest = self._load_manifest()
        datasets: Dict[str, Any] = manifest.get("datasets", {})
        datasets["daily"] = self._scan_daily(pq, datasets.get("daily", {}))
        for ds in DATASETS:
            inv = self._scan_flavor(ds, datasets.get(ds, {}))
            if inv:
                datasets[ds] = inv
        manifest["datasets"] = datasets
        self._save_manifest(manifest)
        self.manifest = manifest
        return manifest

    def _scan_daily(self, pq, prev: Mapping[str, Any]) -> Dict[str, Any]:
        n_syms, n_rows, last_date = 0, 0, None
        for mkt in ("sh", "sz", "bj"):
            path = self.daily_path(mkt)
            if not path.exists():
                continue
            table = pq.read_table(path)
            n_rows += table.num_rows
            codes = table["code"].to_pylist() if table.num_rows else []
            n_syms += len(set(codes))
            dates = [str(d) for d in table["trade_date"].to_pylist()]
            if dates:
                day = max(dates)
                last_date = day if last_date is None else max(last_date, day)
        return {
            "symbols": n_syms,
            "rows": n_rows,
            "last_date": last_date,
            "adjustment": prev.get("adjustment", "none"),
            "source": prev.get("source", "tdx-local vipdoc/lday"),
        }

    def _scan_flavor(self, ds: str, prev: Mapping[str, Any]) -> Dict[str, Any]:
        part = self.flavor_dir(ds)
        files = sorted(part.glob("*.parquet")) if part.exists() else []
        if not files:
            return {}
        import pyarrow.parquet as pq

        rows = sum(pq.read_table(f).num_rows for f in files)
        return {
            "days": len(files),
            "rows": rows,
            "last_date": files[-1].stem,
            "source": prev.get("source", "instock-mysql"),
        }

    # ------------------------------------------------------------------- daily K

    def daily_path(self, market: str) -> Path:
        return self.root / "daily" / f"{market}.parquet"

    def write_daily_market(
        self, market: str, df: pd.DataFrame, source: str, adjustment: str = "none"
    ) -> int:
        """Write/replace one market's daily-K OHLCV parquet (code,trade_date,…)."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        required = ["code", "trade_date", "open", "high", "low", "close", "volume"]
        missing = set(required) - set(df.columns)
        if missing:
            raise ValueError(f"write_daily_market: missing columns {sorted(missing)}")
        cols = required + (["amount"] if "amount" in df.columns else [])
        out = df[cols].copy()
        out["trade_date"] = out["trade_date"].astype("string")
        out = out.drop_duplicates(subset=["code", "trade_date"]).sort_values(
            ["code", "trade_date"]
        )
        path = self.daily_path(market)
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(out, preserve_index=False)
        pq.write_table(table, path, compression="zstd")
        datasets = self.manifest.setdefault("datasets", {})
        datasets["daily"] = {
            "adjustment": adjustment,
            "source": source,
            "markets": [m for m in ("sh", "sz", "bj") if self.daily_path(m).exists()],
        }
        return int(out["code"].nunique())

    def read_daily(
        self,
        codes: Union[str, List[str]],
        start: Optional[str] = None,
        end: Optional[str] = None,
        markets: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """L2 read contract: ``{code: OHLCV DataFrame(index=trade_date)}`` + attrs."""
        import pyarrow.parquet as pq

        if isinstance(codes, str):
            codes = [codes]
        wanted = {c.upper() for c in codes}
        daily_inv = self.manifest.get("datasets", {}).get("daily", {})
        attrs = {
            "quote_currency": "CNY",
            "currency_conversion": "none",
            "adjustment": daily_inv.get("adjustment", "none"),
        }
        out: Dict[str, pd.DataFrame] = {}
        for mkt in markets or ("sh", "sz", "bj"):
            path = self.daily_path(mkt)
            if not path.exists():
                continue
            table = pq.read_table(path, columns=["code", "trade_date", "open", "high", "low", "close", "volume"] + (["amount"] if "amount" in pq.read_schema(path).names else []))
            df = table.to_pandas()
            df = df[df["code"].str.upper().isin(wanted)]
            start_s, end_s = start, end
            if start:
                df = df[df["trade_date"] >= start_s]
            if end:
                df = df[df["trade_date"] <= end_s]
            for code, sub in df.groupby("code"):
                sub = sub.sort_values("trade_date")
                sub["trade_date"] = pd.to_datetime(sub["trade_date"])
                sub = sub.set_index("trade_date")
                sub = sub.drop(columns=["code"])
                sub.attrs = attrs
                out[str(code)] = sub
        return out

    # ------------------------------------------------------------------- flavor

    def flavor_dir(self, dataset: str) -> Path:
        # flavor/<DATASETS[ds]>/ — date parquets sit directly inside.  (The
        # "YYYY-MM-DD" in dataset_to_path is a template placeholder, NOT a
        # literal folder, so we anchor on flavor/<name>.)
        return self.root / "flavor" / DATASETS[dataset]

    def write_flavor(self, dataset: str, df: pd.DataFrame, source: str) -> None:
        """Write per-date cross-section parquets for one flavor dataset."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        if dataset not in DATASETS:
            raise KeyError(f"unknown flavor dataset {dataset!r}; registry keys: {sorted(DATASETS)}")
        if "date" not in df.columns:
            raise ValueError("flavor cross-sections must carry a 'date' column (YYYY-MM-DD)")
        part = self.flavor_dir(dataset)
        part.mkdir(parents=True, exist_ok=True)
        # one parquet per trading date, atomic per file
        for day, sub in df.groupby(df["date"].astype(str)):
            day_f = part / f"{day}.parquet"
            if day_f.exists():
                old = pq.read_table(day_f).to_pandas()
                sub = pd.concat([old, sub], ignore_index=True).drop_duplicates(
                    subset=["code"], keep="last"
                )
            table = pa.Table.from_pandas(sub.reset_index(drop=True), preserve_index=False)
            pq.write_table(table, day_f, compression="zstd")
        datasets = self.manifest.setdefault("datasets", {})
        datasets[dataset] = {"source": source}

    def read_flavor(self, dataset: str, date: Optional[str] = None) -> pd.DataFrame:
        part = self.flavor_dir(dataset)
        files = [part / f"{date}.parquet"] if date else sorted(part.glob("*.parquet"))
        if not files or not all(f.exists() for f in files):
            return pd.DataFrame()
        import pyarrow.parquet as pq

        return pq.read_table(files).to_pandas()

    # ------------------------------------------------------------------- duckdb

    def duckdb_query(self, sql: str) -> pd.DataFrame:
        """Cross-warehouse ad-hoc query (read-only). Pre-registered views:

        - ``daily``       : all-market daily-K (code, trade_date, OHLCV, amount)
        - ``flavor_<ds>`` : one flavor dataset (all dates concatenated)
        """
        import duckdb

        con = duckdb.connect()
        if any(self.daily_path(m).exists() for m in ("sh", "sz", "bj")):
            con.execute(
                f"CREATE VIEW daily AS SELECT * FROM read_parquet('{self.root / 'daily' / '*.parquet'}')"
            )
        for ds in DATASETS:
            part = self.flavor_dir(ds)
            if part.exists() and any(part.glob("*.parquet")):
                con.execute(
                    f"CREATE VIEW flavor_{ds} AS SELECT * FROM read_parquet('{part}/*.parquet')"
                )
        return con.execute(sql).df()

    # ------------------------------------------------------------------- sync hook

    def finalize_sync(self, sources: Mapping[str, str], **extra: Any) -> Dict[str, Any]:
        manifest = self.refresh_manifest()
        manifest["last_sync_at"] = _now_iso()
        manifest["sources"] = dict(sources)
        manifest.update(extra)
        self._save_manifest(manifest)
        self.manifest = manifest
        return manifest
