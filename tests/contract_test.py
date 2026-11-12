"""P1 contract test — shared fixture gate for the cn-market-data package.

Guards the P1 acceptance criteria (见 docs/unified-a-share-data-design.md §11):
1. Package imports without Vibe-Trading internals (切缝 ①②③).
2. A-share fallback chain is the canonical subset.
3. cache_version gating + frame attrs contract present.
4. Unknown source / unavailable source fails loudly.
5. Flavor schema datasets are frozen in the registry.

Run: python tests/contract_test.py  (needs pandas; run under any env with pandas)
"""

import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent  # cn-market-data repo root
_SRC = _PKG_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import cn_market_data  # noqa: E402
from cn_market_data import base as base_mod  # noqa: E402
from cn_market_data import registry as reg  # noqa: E402
from cn_market_data.flavor import schema as flavor_schema  # noqa: E402


class TestPackageIndependence(unittest.TestCase):
    """切缝 ①②③: no actual Vibe-Trading internal imports in the package."""

    def test_no_vibe_internal_imports(self):
        import re

        root = _SRC
        for py in sorted(root.rglob("*.py")):
            txt = py.read_text(encoding="utf-8")
            for bad in (
                r"from\s+src\.config\.accessor\b",
                r"import\s+src\.config\.accessor\b",
                r"from\s+backtest\.loaders\b",
                r"import\s+backtest\.loaders\b",
            ):
                self.assertIsNone(
                    re.search(bad, txt), f"{py}: Vibe import ref left: {bad}"
                )


class TestFallbackChain(unittest.TestCase):
    def test_a_share_chain(self):
        self.assertEqual(
            reg.FALLBACK_CHAINS["a_share"],
            ["tencent", "eastmoney", "baostock", "akshare", "tushare", "local"],
        )

    def test_a_share_adjustment(self):
        self.assertEqual(reg.a_share_adjustment("tencent"), "split_divident_additive")
        self.assertEqual(reg.a_share_adjustment("eastmoney"), "split_divident")

    def test_unknown_source_fails_loudly(self):
        with self.assertRaises(cn_market_data.NoAvailableSourceError):
            reg.get_loader_cls_with_fallback("not_a_real_source")


class TestCacheContract(unittest.TestCase):
    def test_cache_version_gated(self):
        self.assertEqual(base_mod._LOADER_CACHE_VERSION, 9)
        self.assertEqual(
            base_mod._LOADER_FRAME_METADATA_ATTRS,
            ("quote_currency", "currency_conversion", "adjustment"),
        )
        payload = base_mod._loader_cache_payload(
            source="tencent",
            symbol="000001",
            timeframe="1D",
            start_date="2026-01-01",
            end_date="2026-01-05",
            fields=None,
        )
        self.assertEqual(payload.get("version"), 9)


class TestFlavorSchema(unittest.TestCase):
    def test_flavor_datasets_frozen(self):
        for ds in ("lhb", "money_flow", "f10", "zttp", "news", "macro", "event"):
            self.assertIn(ds, flavor_schema.DATASETS, f"dataset {ds} missing from registry")


class TestL3Warehouse(unittest.TestCase):
    """L3 共享仓: L2 read contract + cache_version gate (skips without pyarrow)."""

    @classmethod
    def setUpClass(cls):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pyarrow not installed — L3 warehouse tests skipped")

    def test_daily_read_contract_and_version_gate(self):
        import json
        import tempfile

        import pandas as pd
        from cn_market_data.warehouse.store import CacheVersionMismatch, L3Store

        with tempfile.TemporaryDirectory() as tmp:
            store = L3Store(tmp)
            store.init_warehouse()
            df = pd.DataFrame(
                {
                    "code": ["600406", "600406"],
                    "trade_date": pd.to_datetime(["2026-11-10", "2026-11-11"]),
                    "open": [23.6, 23.4],
                    "high": [23.7, 23.5],
                    "low": [23.3, 23.0],
                    "close": [23.4, 23.1],
                    "volume": [1000, 2000],
                    "amount": [2.0e5, 4.0e5],
                }
            )
            self.assertEqual(store.write_daily_market("sh", df, source="test"), 1)
            out = store.read_daily(["600406"])
            self.assertIn("600406", out)
            self.assertEqual(
                list(out["600406"].columns),
                ["open", "high", "low", "close", "volume", "amount"],
            )
            self.assertEqual(out["600406"].attrs["adjustment"], "none")
            self.assertEqual(len(out["600406"]), 2)

            # cache_version 门控：磁盘版本漂移后，旧读者必须拒绝读
            m = json.loads(store.manifest_path.read_text(encoding="utf-8"))
            m["cache_version"] = 99
            store.manifest_path.write_text(json.dumps(m), encoding="utf-8")
            with self.assertRaises(CacheVersionMismatch):
                L3Store(tmp)

    def test_flavor_partition_roundtrip(self):
        import tempfile

        import pandas as pd
        from cn_market_data.warehouse.store import L3Store

        with tempfile.TemporaryDirectory() as tmp:
            store = L3Store(tmp)
            store.init_warehouse()
            df = pd.DataFrame(
                {
                    "code": ["600406", "000001"],
                    "date": ["2026-10-08", "2026-10-08"],
                    "ranking_times": [1, 2],
                    "reason": ["x", "y"],
                }
            )
            store.write_flavor("lhb", df, source="test")
            back = store.read_flavor("lhb", "2026-10-08")
            self.assertEqual(len(back), 2)
            self.assertEqual(sorted(back["code"]), ["000001", "600406"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
