# cn-market-data — A股数据统一包（P1 已交付骨架）

**包名**：`cn-market-data`（独立仓 `D:/git_project/cn-market-data`）
**覆盖**：TSP / PanWatch / InStock / Vibe-Trading / Hermes（含 cron 薄壳）+ 未来项目
**当前状态**：P1（抽包 + 切缝 + 契约 + L2 合约）已交付骨架；P2 待接入 InStock+Hermes；**本地通达信离线兜底源已实现**（`vipdoc/lday/*.day` 解析，网络源优先、离线兜底）。
**设计基线**：`docs/unified-a-share-data-design.md`（v0.1）

## 三层架构
```
L3 共享仓（盘后 sync 写，读仓消费，只存已收盘日频数据）
  ~/.cn-market-data/warehouse/  (或 VIBE_CN_MARKET_DATA_WAREHOUSE 指定，默认 D:/cn-market-data/warehouse)
L2 数据合约（OHLCV dict[sym,DataFrame]+attrs；flavor schema 统一味道数据）
L1 源访问（唯一网络层，A股 subset；registry + loaders + fallback 链）
```

## P1 已实现
- **切缝 ①②③**：`config.py` 替代 Vibe `src.config.accessor`；`registry` 包级 A股 fallback；无 Vibe 内部 import。
- **L1 抽取**：A股 loaders（tencent/eastmoney/akshare/baostock/mootdx/tushare/local）+ additive_conversion + base/registry/cn_adjust/_symbol_utils/_http，包路径已切到 `cn_market_data.*`。
- **L2 合约**：`OHLCV` 列 + `attrs{quote_currency,currency_conversion,adjustment}`；`flavor/schema.py` 味道数据统一 schema（数据集注册表冻结）。
- **L3 仓**：运行时目录 `D:/cn-market-data/warehouse/`（manifest.tpl + .gitkeep），cache_version=9 门控。
- **contract-test**：`tests/contract_test.py` 守 P1 验收（独立、fallback、cache、schema）。

## 关键契约
- A股 fallback 链：`tencent → eastmoney → baostock → akshare → tushare → local`（mootdx 仅显式 source 入口，默认链不含）。
- cache_version=9（复用 Vibe `_LOADER_CACHE_VERSION`），布局/单位/复权变更必须升版本。
- Hermes cron 薄壳铁律：cron 一律只读 L3 仓，不 import `cn_market_data`。

## 本地通达信源（P2 离线兜底）
- `local_loader.py`：解析通达信 `vipdoc/{sh,sz,bj}/lday/*.day`（标准日线缓存，32 字节/记录：date int32 + open/high/low/close×100 + amount float32 + volume int32 + reserved）。
- 目录路径由 `TDX_LOCAL_VIPDOC` 环境变量覆盖，默认 `D:/Suntech/New_tdx/vipdoc`；符号支持 `sh600030`/`600030` 等（按前缀推断市场）。
- 价格 ÷100 还原为元，默认不复权（`adjustment="none"`）；网络源优先，本源仅离线兜底。
- 已验证：sz000001 ~11.78 元、sh600030 ~25.40 元、bj899050 ~1012 元，OHLC 一致、attrs 正确。
- 注意：`T0002/cache` 下 `.~~~day` 被运行中的 TDX 进程锁住不可读；用 `vipdoc/lday/*.day` 作为本地源更稳定（可稳定读取）。

## 验收
`python tests/contract_test.py` 全绿即 P1 通过。
