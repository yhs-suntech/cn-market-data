"""Post-close L3 sync job: InStock MySQL (flavor) + TDX local (daily K) → L3 warehouse.

Owner: Vibe-Trading post-close cron (P2).  Run with the Hermes runtime python
(pyarrow + duckdb installed) — the Hermes *cron thin shell* itself only reads
the finished warehouse, it never imports this module (zero-dependency iron rule).

Usage::

    python -m cn_market_data.warehouse.sync_instock --all \
        [--warehouse-root D:/cn-market-data/warehouse] \
        [--mysql-host localhost --mysql-port 3306 --mysql-user root \
         --mysql-password root --mysql-db instockdb] \
        [--tdx-vipdoc D:/Suntech/New_tdx/vipdoc] \
        [--universe instock | all]

Env overrides: CNM_DATA_WAREHOUSE, TDX_LOCAL_VIPDOC, CNM_DATA_MYSQL_* (same names
as the flags, upper-snake).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from cn_market_data.warehouse.store import L3Store, warehouse_root

# InStock MySQL table → L2 flavor dataset key (registry in flavor/schema.py)
FLAVOR_TABLE_MAP: Dict[str, str] = {
    "cn_stock_lhb": "lhb",
    "cn_stock_blocktrade": "dzjy",
    "cn_stock_bonus": "fhps",
    "cn_stock_limitup_reason": "zttp",
}


def _mysql_dsn(args) -> str:
    host = args.mysql_host or os.getenv("CNM_DATA_MYSQL_HOST", "localhost")
    port = str(args.mysql_port or int(os.getenv("CNM_DATA_MYSQL_PORT", "3306")))
    user = args.mysql_user or os.getenv("CNM_DATA_MYSQL_USER", "root")
    pw = args.mysql_password or os.getenv("CNM_DATA_MYSQL_PASSWORD", "root")
    db = args.mysql_db or os.getenv("CNM_DATA_MYSQL_DB", "instockdb")
    return f"host={host} port={port} user={user} password={pw} database={db}"


def _attach_instock(con, dsn: str) -> None:
    con.execute("INSTALL mysql; LOAD mysql;")
    con.execute(f"ATTACH '{dsn}' AS ins (TYPE mysql)")


def sync_flavor_from_instock(store: L3Store, dsn: str) -> Dict[str, int]:
    """Export InStock flavor tables into L3 flavor partitions (per-date parquets)."""
    import duckdb

    con = duckdb.connect()
    _attach_instock(con, dsn)
    written: Dict[str, int] = {}
    for table, ds in FLAVOR_TABLE_MAP.items():
        sql = f"SELECT * FROM ins.{table}"
        df = con.execute(sql).df()
        if df.empty:
            continue
        df["date"] = df["date"].astype(str)
        store.write_flavor(ds, df, source=f"instock-mysql:{table}")
        written[ds] = len(df)
    con.close()
    return written


def _universe_from_instock(dsn: str) -> List[str]:
    import duckdb

    con = duckdb.connect()
    _attach_instock(con, dsn)
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM ins.cn_stock_spot ORDER BY code"
    ).fetchall()]
    con.close()
    return [c for c in codes if c]


def sync_daily_from_tdx(
    store: L3Store,
    vipdoc: Optional[str] = None,
    universe: Optional[List[str]] = None,
    market_filter: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Parse TDX local .day files → per-market daily-K parquets in the L3 warehouse."""
    from cn_market_data.loaders.local_loader import _resolve_day_file, _parse_day_file, _infer_market

    if vipdoc is None:
        vipdoc = os.getenv("TDX_LOCAL_VIPDOC", r"D:/Suntech/New_tdx/vipdoc")
    root = Path(vipdoc)

    buckets: Dict[str, List[pd.DataFrame]] = {"sh": [], "sz": [], "bj": []}
    if universe is not None:
        for code in universe:
            path = _resolve_day_file(root, str(code))
            if path is None:
                continue
            df = _parse_day_file(path)
            if df.empty:
                continue
            df["code"] = str(code)
            buckets[_infer_market(str(code))].append(df)
    else:
        for market in ("sh", "sz", "bj"):
            lday = root / market / "lday"
            if not lday.exists():
                continue
            for path in sorted(lday.glob("*.day")):
                code = path.stem[2:]  # strip sh/sz/bj prefix
                df = _parse_day_file(path)
                if df.empty:
                    continue
                df["code"] = code
                buckets[market].append(df)

    written: Dict[str, int] = {}
    for market, frames in buckets.items():
        if market_filter and market not in market_filter:
            continue
        if not frames:
            continue
        out = pd.concat(frames, ignore_index=True)
        out = out[["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]]
        n = store.write_daily_market(market, out, source="tdx-local vipdoc/lday", adjustment="none")
        written[market] = n
    return written


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--all", action="store_true", help="sync daily + flavor")
    p.add_argument("--daily", action="store_true", help="sync daily K only")
    p.add_argument("--flavor", action="store_true", help="sync flavor only")
    p.add_argument("--universe", choices=["instock", "all", "none"], default="instock",
                   help="daily K universe: instock active codes (default) / all lday files / none")
    p.add_argument("--warehouse-root", default=None)
    p.add_argument("--tdx-vipdoc", default=None)
    p.add_argument("--mysql-host", default=None)
    p.add_argument("--mysql-port", type=int, default=None)
    p.add_argument("--mysql-user", default=None)
    p.add_argument("--mysql-password", default=None)
    p.add_argument("--mysql-db", default=None)
    args = p.parse_args(argv)

    do_daily = args.daily or args.all
    do_flavor = args.flavor or args.all
    if not (do_daily or do_flavor):
        p.error("nothing to do — pass --all / --daily / --flavor")

    store = L3Store(args.warehouse_root or warehouse_root())
    if not store.manifest_path.exists():
        store.init_warehouse()

    sources: Dict[str, str] = {}
    flavor_report: Dict[str, int] = {}
    daily_report: Dict[str, int] = {}
    dsn = _mysql_dsn(args)

    if do_daily:
        universe: Optional[List[str]] = None
        if args.universe == "instock":
            universe = _universe_from_instock(dsn)
            print(f"[daily] instock universe: {len(universe)} codes")
            sources["daily_universe"] = "instock.cn_stock_spot"
        daily_report = sync_daily_from_tdx(store, args.tdx_vipdoc, universe=universe)
        sources.setdefault("daily", "tdx-local vipdoc/lday (adjustment=none)")
    if do_flavor:
        flavor_report = sync_flavor_from_instock(store, dsn)
        for table, ds in FLAVOR_TABLE_MAP.items():
            if ds in flavor_report:
                sources[ds] = f"instock-mysql:{table}"

    manifest = store.finalize_sync(sources)
    print(f"[sync] daily markets: {daily_report}")
    print(f"[sync] flavor rows:   {flavor_report}")
    print(f"[sync] manifest -> {store.manifest_path}")
    print(f"[sync] cache_version={manifest.get('cache_version')} last_sync_at={manifest.get('last_sync_at')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
