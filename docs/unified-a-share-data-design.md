# A股数据统一化设计文档

**包名（暂定）**：`cn-market-data`（独立仓 `D:/git_project/cn-market-data`）
**覆盖范围**：TSP / PanWatch / InStock / Vibe-Trading / Hermes（含其 cron 薄壳）+ 未来类似项目
**状态**：设计定稿，待拍板（A 拓扑 + 上 L3 仓）
**日期**：2026-11-11
**版本**：v0.1（设计基线）

---

## 0. TL;DR（给老板快速看）

1. 现状：5 个项目各自重写 A股取数（通用 OHLCV + A股味道数据），味道数据有 **4 份**重复、痛点最大；Hermes 侧 a-stock-data + eltdx 是第 4 份。
2. 推荐拓扑：**独立新包 `cn-market-data`**（从 Vibe-Trading `backtest.loaders` 抽 A股 subset，切 3 道缝解耦）+ **一个共享 Parquet 仓**（网络只发生一次，5 项目与全部 cron 读本地仓，满足 cron 薄壳零依赖）。
3. 统一契约三层：**L1 源访问**（`DataLoaderProtocol` + fallback + 防封/降级 + `cache_version`）→ **L2 数据合约**（OHLCV + 味道数据统一 schema，各项目薄适配不碰原 schema）→ **L3 共享仓**（盘后 sync 写、读仓消费）。
4. 风险可控：爆炸半径用 semver + `cache_version` 门控 + 5 项目共享 contract-test fixture 守门；Vibe-Trading churn 抽成 snapshot 后是移植活而非被动挨打。
5. 落地分 P0~P4，先零风险定方向 → 抽包（不改引用）→ 先吃 InStock + Hermes → TSP/PanWatch/Vibe-Trading 切包 → 未来项目零爬虫。

---

## 1. 问题

A股数据在各项目里被重复建设，同一段「东财/腾讯 HTTP 爬虫」被 4~5 处各自实现，且每个实现各自踩一遍数据源坑：

- 东财封 IP、腾讯 K线 501 已下线、mootdx 服务死、北交所缺失——这些坑在 a-stock-data skill、InStock、PanWatch、Vibe-Trading 各自重学一遍。
- 任何一处数据源变更（源调整、复权口径、防封策略），要同步改 4~5 处，容易漏改导致线上线下口径不一致。
- 未来再出类似项目，又要再从零搭一套取数层。

**目标**：把 A股取数收敛为**一个事实源**，后续项目零爬虫直接依赖；网络层只发生一次，下游全部读本地。

---

## 2. 现状盘点（带证据）

| 项目 | 取数层形态 | 数据契约 | 源 | 缓存 | 重复度 |
|---|---|---|---|---|---|
| **Vibe-Trading** | `backtest.loaders`：40+ loader + registry + fallback chain + 内容寻址 Parquet 缓存 + provenance | `dict[sym, pd.DataFrame]` | 多市场 40 源（A股 tencent/eastmoney/baostock/akshare/tushare/mootdx） | Parquet，`cache_version=9` | 通用 OHLCV 最全 |
| **TSP** | `app/data_providers`：Polars `MarketDataProvider` Protocol + registry + normalizer | `pl.DataFrame` | tickflow / akshare(custom) | 存储引擎 | 低（抽象干净，但与别人不共享） |
| **PanWatch** | `packages/marketdata`：可独立 pip 安装的多 vendor 包，主备 failover + TTL + health | typed dataclass（Quote/Bar/…） | 东财/腾讯/新浪/同花顺 | TTL + health | 味道数据 vendor 较多 |
| **InStock** | `instock/core/crawling/`：14 个 requests 爬虫 + `stockfetch.py` 平铺函数 | pandas + per-code pickle | 腾讯（9月为绕东财封IP重写） | 本地 pickle | **味道数据最痛（最重复）** |
| **Hermes** | `a-stock-data` skill（87 端点/34 源/15 层）+ `eltdx`/tdx MCP | 脚本内联 | 34 源 | 无（依赖 provider 内缓存） | 运维侧，且 cron 薄壳零依赖铁律 |

> 关键结论：Vibe-Trading 的 `backtest.loaders` 是**事实上的共享源层**（最强），但被自身 backtest 引擎 `backtest.engines._market_hooks` 与 `src.config.accessor` 三道缝耦合、且上游仍在演进（issues #1062/#1410/#1541/#1729）。因此不直接让它当共享依赖，而是**抽取 A股 subset 为独立包**。

---

## 3. 设计原则

1. **独立包解耦 churn**：A股 subset 独立成 `cn-market-data`，不依赖 Vibe-Trading 内部 `backtest.engines`，上游演进不再传导到 5 项目。
2. **网络层隔离 + 仓消费**：网络取数只在包内（及盘后 sync）发生一次；5 项目与 cron **读本地 Parquet 仓**。这同时满足 Hermes no_agent cron「壳层零第三方依赖」铁律——cron 不 import 包，只读仓。
3. **薄适配，不替换下游 schema**：L1 输出统一 pandas 契约，各项目在其上做自己需要的形状（TSP→Polars、PanWatch→dataclass、InStock→pandas），不把 Vibe-Trading 的 loader、InStock 的 pickle、PanWatch 的 vendor 逻辑搬进包。
4. **contract-test 守门**：5 项目共用同一份 fixture + 契约测试，保证抽包后各方取数口径不漂移。
5. **缓存版本门控**：复用 Vibe-Trading 的 `cache_version` 机制，任何 payload 布局/单位/复权口径变更必须升版本，旧缓存永不匹配（Vibe-Trading 已实现 `_LOADER_CACHE_VERSION=9`，直接复用其纪律）。

---

## 4. 目标架构（三层）

```
┌──────────────────────────────────────────────────────────────────────┐
│ L3 共享仓（只读，无网络）                                          │
│   Parquet/DuckDB 仓，按 cache_version 分区                           │
│   ~/.cn-market-data/warehouse/{daily,flavor}/{version}/...          │
│   全部 5 项目 + 全部 cron 只读此仓                                     │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 读仓（日线/味道数据）
┌───────────────────────────────▼──────────────────────────────────────┐
│ L2 数据合约（各项目自带薄适配，不碰原爬虫）                            │
│   OHLCV：dict[sym,DataFrame] + attrs{quote_currency,adjustment}       │
│   味道数据：schema.py 统一 dataclass + DataFrame 列契约                 │
│   TSP→.to_polars()   PanWatch→vendor→dataclass   InStock→pandas        │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ 调 L1（实时/盘中，带 TTL，不走仓）
┌───────────────────────────────▼──────────────────────────────────────┐
│ L1 源访问（唯一网络层，全项目共享）                                    │
│   cn_market_data.base   DataLoaderProtocol + registry + retry/budget   │
│   cn_market_data.registry  FALLBACK_CHAINS(A股) + resolve_loader        │
│   cn_market_data.loaders  tencent/sina/eastmoney/akshare/baostock/     │
│                   tushare/mootdx/local + cn_adjust                   │
│   cn_market_data.flavor       味道数据统一采集（合并 PanWatch vendors   │
│                          + InStock 14 爬虫 + a-stock-data 端点）       │
└──────────────────────────────────────────────────────────────────────┘

网络取数 → L1 采集 → (实时:带 TTL 缓存直供) / (盘后:写 L3 仓)
L3 仓 → L2 适配 → 各项目 / cron
```

---

## 5. L1 契约（源访问，唯一网络层）

### 5.1 核心接口（取自 Vibe-Trading `backtest/loaders/base.py`，可直接复用其原型）

```python
# cn_market_data.base
class NoAvailableSourceError(Exception): ...

@runtime_checkable
class DataLoaderProtocol(Protocol):
    name: str
    markets: set[str]
    requires_auth: bool

    def is_available(self) -> bool: ...

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        # {symbol: DataFrame(trade_date, open, high, low, close, volume[, amount, vwap, funding_rate])}
        ...
```

**OHLCV DataFrame 列契约（统一）**：
`trade_date, open, high, low, close, volume`（必需）+ `amount, vwap, funding_rate`（可选，按 loader 提供）。

**帧属性 attrs（统一，供下游复权/币种判定）**：
`quote_currency`、`currency_conversion`、`adjustment`（取值：`raw / split / split_dividend / split_dividend_additive / na / unknown`）。

**A股 fallback 链（统一，来自 Vibe-Trading registry `FALLBACK_CHAINS["a_share"]`）**：
`["tencent", "eastmoney", "baostock", "akshare", "tushare", "local"]`
（mootdx 已于 2026-10-08 退役，不进默认链，仅保留显式 `source="mootdx"` 入口——与 a-stock-data skill 记录一致）。

**缓存**：内容寻址 `sha256(source,symbol,timeframe,start,end,fields)` → 落 Parquet，**仅缓存 end_date 已收盘（< 今日）的区间**（Vibe-Trading `loader_cache_range_is_final` 同规则），带 `cache_version` 门控。

### 5.2 抽取清单（从 Vibe-Trading `backtest/loaders/` 移入）

`base.py`、`registry.py`、`cn_adjust.py`、`_http.py`、`_symbol_utils.py`，以及 A股相关 loader：`tencent_loader`、`sina_loader`、`eastmoney_loader`、`akshare_loader`、`baostock_loader`、`mootdx_loader`、`tushare.py`、`local_loader`。

### 5.3 切 3 道缝（解耦 Vibe-Trading 依赖）

| 缝 | Vibe-Trading 原依赖 | 改为 |
|---|---|---|
| ① registry 配置访问 | `from src.config.accessor import get_env_value`（env 覆盖刷新） | 包内独立 `config.py`（`get_env_config`/`get_env_value` 本地实现） |
| ② 市场判定 | `from backtest.engines._market_hooks import _detect_market` | 包内纯函数 `detect_market(code) -> market` |
| ③ 缓存开关 | `from src.config.accessor import get_env_config`（cache 开关/根） | 包内独立 `config.py` |

> 切缝后包可独立 `pip install`，不 import Vibe-Trading 任何内部模块。

### 5.4 味道数据统一采集（L1 的 flavor 子包，合并 4 处重复）

来源合并：PanWatch `marketdata` vendors（东财/腾讯/新浪/同花顺）、InStock `core/crawling/` 14 个爬虫、a-stock-data skill 对应端点。**统一成一套采集器，输出统一 schema（见 6.2）。**

---

## 6. L2 数据合约（各项目薄适配）

### 6.1 OHLCV 适配（三项目各一行）

| 项目 | 适配 |
|---|---|
| TSP | `pl.DataFrame.to_polars()` 转 Polars，保留 attrs 的复权/币种元数据 |
| PanWatch | vendor 结果 → `Quote/Bar` dataclass 映射（保留 health/主备记录） |
| InStock | 直接用 pandas `dict[sym,DF]`，与原 `stockfetch.py` 调用形态对齐 |

### 6.2 味道数据统一 schema（核心去重点）

**原则**：每类数据集 = 1 个 typed dataclass + 1 个 DataFrame 列契约；统一按 `(symbol, date)` 组织，落仓按数据集分 parquet 分区。

**统一数据集清单**（覆盖 a-stock-data 层 + PanWatch vendor + InStock 14 爬虫）：

| 数据集 | 代表字段（核心） | 主要来源 |
|---|---|---|
| 龙虎榜 | code, date, ranking_times, 买卖席位 TOP5 | 东财龙虎榜 / InStock `stock_lhb_*` |
| 资金流 | code, date, 主力净额/占比、16档大小单 | 东财资金流 / 腾讯 / eltdx |
| 大宗交易 | code, date, 成交价/量/对手方 | InStock `stock_dzjy_em` |
| 分红 | code, date, 每股分红/送转、支付比例 | InStock `stock_fhps_em` |
| 融资融券 | code, date, 融资余额/融券余额 | 东财两融 |
| 股东户数 | code, date, 户数/变化/均持股 | 东财股东户数 |
| 财报/F10 | 收入/净利/毛利率/ROE/资产负债(分期) | 通达信 F10 / 新浪三表 / 东财 |
| 涨停池 | code, date, 涨停/炸板/跌停家数、涨停原因 | 东财四池 / 同花顺 |
| 新闻/快讯 | code, date, title/content/time | 东财新闻 / 财联社 / 华尔街见闻 |
| 北向资金 | date, 净买入/累计 | 同花顺 / 东财 |
| 宏观利率 | date, 社融/PMI/中债/回购/LPR | a-stock-data macro 端点 |
| 指数成分/权重/估值 | index, 成分/权重/PE/股息率 | a-stock-data index 端点 |
| 事件驱动 | code, date, 业绩预告/机构调研/增减持/回购/质押/解禁 | 东财事件端点 |
| 可转债 | 条款/转股价值/溢价率 | 东财可转债 |
| 期货/大宗 | symbol, date, OHLCV | a-stock-data 期货端点 |

> 字段以 a-stock-data `references/layerNN-*.md` 各端点实际返回为准，P1 实现时逐数据集冻结字段表，放进包 `flavor/schema.py`。

---

## 7. L3 共享仓（盘后 sync 写，读仓消费）

### 7.1 布局

```
~/.cn-market-data/warehouse/                 # 默认根，可配置覆盖
  manifest.json                              # cache_version + 各数据集 last_update + row_count
  daily/{cache_version}/YYYY/MM/daily_bars.parquet
  flavor/{cache_version}/<dataset>/YYYY-MM-DD/<dataset>.parquet
```

- 根默认 `~/.cn-market-data`（或共享 D 盘路径，5 项目+进程一致），可在 `config.py` 配置。
- 仓**只存已收盘数据**（日线/味道数据均为日频）；盘中实时/分钟级**不走仓**，走 L1 带 TTL 直供（如 PanWatch 盘中、早盘信号）。

### 7.2 写入（盘后 sync job）

每日收盘后由一个 sync job（可由 Vibe-Trading 或独立调度跑）调用 L1 采集，**只写一次**，全仓共享：
- 全市场日线 → `daily/{version}/`
- 各味道数据集 → `flavor/{version}/<dataset>/`
- 每次写更新 `manifest.json` 的 last_update + row_count。
- 写入受 `cache_version` 门控：布局/单位/复权口径变更必须升版本，旧版本保留、新代码只读新版本。

### 7.3 读取

5 项目 + 全部 cron **只读仓**，不 import 包、零第三方依赖：
- 日线/味道数据：`pandas.read_parquet` 读对应分区。
- 需要盘中实时/分钟级：直接调 L1（带 TTL），不走仓。

---

## 8. 各项目接入动作

| 项目 | 接入方式 | 改动量 | 风险 |
|---|---|---|---|
| **InStock** | 删 `core/crawling/` 14 爬虫 + `stockfetch.py` 平铺 → 调 L1 / 读仓 | 大（删重复），但纯删除 | 低 |
| **Hermes** | a-stock-data skill 变薄路由壳（路由到 L1/仓）；eltdx/tdx MCP 作 L1 可选 loader；**所有 cron 改读 L3 仓**（保持薄壳零依赖） | 中（逐条验 cron） | 中 |
| **Vibe-Trading** | 自身改读包（dogfooding），pin 上游 tag | 中 | 中 |
| **TSP** | Polars Provider 改 L1 薄适配，删 custom GenericHTTP 爬虫 | 中 | 中 |
| **PanWatch** | 味道数据 vendors 让位于 L1；保留 dataclass + health 解读 | 中 | 中 |

> Hermes 特别注意：cron 薄壳零第三方依赖铁律，**cron 一律只读 L3 仓，不 import `cn_market_data`**（否则拖入 pandas/httpx/duckdb 违反铁律）。

---

## 9. 风险面与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 爆炸半径：5 项目 + 全部日常 cron 挂一个包 | 一次坏 release / 缓存错配 = 全线崩 | semver + 冻结 minor；`cache_version` 门控（复用 Vibe-Trading 机制）；5 项目共享 contract-test fixture；发布前跑 contract-test + 关键 cron dry-run |
| Vibe-Trading 上游 churn | 抽包后上游再变需手动移植 | pin 上游 tag；定期 diff 上游 loaders；包内锁 A股 subset，不吸全市场变更 |
| 仓 vs 实时混淆 | 盘中/分钟级被误放进仓，刷新慢 | 仓只存盘后日频/味道数据；实时/分钟级走 L1 带 TTL，边界由 sync job 的调度保证（收盘后才写仓） |
| 各项目怪癖（InStock pickle、TSP Polars、PanWatch health） | 适配成本 | 薄适配只做形状转换，不搬原逻辑；怪癖字段在包 schema 里显式标注 |
| 抽包切缝回归 | 切 3 道缝后行为漂移 | 切缝后契约测试（fetch 返回形状、fallback 顺序、复权/币种 attrs）全跑；与 Vibe-Trading 原 loader 对跑同一 fixture |

---

## 10. 路线图（P0~P4）

| 阶段 | 目标 | 主要交付 | 验收 |
|---|---|---|---|
| **P0 定方向** | 零风险定架构与决策 | 5 项目 README 各加「A股取数统一 → cn-market-data / 共享仓」说明；本文档定稿 | 文档拍板通过 |
| **P1 抽包** | 交付 L1 包，不改任何现有引用 | `cn-market-data` 独立仓：base/registry/cn_adjust/loaders/flavor/schema；切 3 道缝；`cache_version` + contract-test；L2 OHLCV 适配 | L1 可独立跑通 A股 fallback 与复权/币种 attrs；contract-test 通过 |
| **P2 先吃 InStock + Hermes** | 去重最大、风险最低 | InStock 删 14 爬虫改调 L1/读仓；Hermes a-stock-data 变薄路由、cron 改读仓（保持薄壳零依赖） | InStock 相关功能跑通；cron 薄壳仍零第三方依赖；contract-test 通过 |
| **P3 TSP + PanWatch + Vibe-Trading 切包** | 全部项目接入 | 三者做薄适配切包；Vibe-Trading 改读包（dogfooding）；盘后 sync job 上线 | 5 项目均可经 L1/仓取数；线上无口径漂移 |
| **P4 未来项目** | 新项目零爬虫 | 新项目 `pip install cn-market-data` / 直接读仓 | 新项目接入即用，无自研取数层 |

---

## 11. 验收标准（P1 起持续守）

- [ ] `cn-market-data` 可独立 `pip install`，不 import Vibe-Trading/InStock/PanWatch 内部模块（仅依赖 pandas + 标准库 + 可选 provider）。
- [ ] L1 能跑通 A股 fallback 链（tencent→eastmoney→baostock→akshare→tushare→local），失败逐级降级，`NoAvailableSourceError` 正确抛出。
- [ ] L1 输出统一 OHLCV 列 + `attrs{quote_currency,adjustment}`，与 Vibe-Trading 原 loader 对跑同一 fixture，口径一致。
- [ ] `cache_version` 门控生效：改 payload/单位/复权 → 升版本 → 旧缓存永不匹配。
- [ ] 味道数据 schema 冻结在 `flavor/schema.py`，4 处原重复源（PanWatch vendors / InStock 14 / a-stock-data 端点）均不再直接对外暴露取数。
- [ ] 5 项目 + 全部 cron 共享 contract-test fixture，全部通过。
- [ ] Hermes cron 薄壳仍满足零第三方依赖（只读仓）。
- [ ] 盘后 sync job 可跑通：全市场日线 + 各味道数据集写仓，`manifest.json` 正确更新。

---

## 12. 决策记录

| 决策 | 结论 | 备注 |
|---|---|---|
| 拓扑 | A = 独立新包 + 共享仓（推荐） | 不直接吃 Vibe-Trading 本体，解耦 churn、满足 cron 薄壳零依赖、未来可扩展；B=吃 Vibe-Trading 本体省活但绑死 churn，暂不选 |
| L3 仓 | 上（P1 起交付） | 满足 cron 薄壳零依赖 + 网络层唯一化 + 未来扩展；盘中/实时走 L1 带 TTL 不走仓 |
| 抽取 | 从 Vibe-Trading `backtest/loaders` 抽 A股 subset | 切 3 道缝（config/detect_market/cache 开关）后独立可装 |

> 待拍板确认后，进入 P1：建仓骨架 + 切缝 + contract-test。
