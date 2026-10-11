# V1 Market Data REST Specification

## Purpose

实现 KCQ-NexusAI 的 market-data V1 REST 协议（probe / instruments/search / bars /
trading-calendar / timeshare），让前端 core 行情层把本连接器当作任意 V1 数据源消费。
成功响应统一 `{data, requestId}` envelope，错误统一 `{error: {code, message}, requestId}`。
契约唯一权威是前端 `packages/core/src/data/provider/protocol/types.ts`；
本规格描述连接器侧的实现承诺。

## Requirements

### Requirement: 错误响应必须使用非 2xx HTTP 状态码

系统 SHALL 在返回错误信封时使用非 2xx HTTP 状态码，映射规则为
`INVALID_REQUEST`/`UNSUPPORTED_CAPABILITY` → 400、`INSTRUMENT_NOT_FOUND` → 404、
`UPSTREAM_UNAVAILABLE`/`INVALID_RESPONSE` → 502、`TIMEOUT` → 504、其余 → 500。

理由：KCQ 的 `httpTransport` **只在 `!res.ok` 分支解析错误信封**
（`protocol/impl/httpTransport.ts:86-99`）；若以 2xx 返回错误信封，前端会因缺少 `data`
字段抛出通用 `FETCH_FAILED` 并**丢弃错误码**，导致 `UNSUPPORTED_CAPABILITY` /
`INSTRUMENT_NOT_FOUND` 无法触发 `SourceRouter` 的源流转。

#### Scenario: 不支持的周期

- **WHEN** 请求 `period` 不是 `daily`
- **THEN** HTTP 400，`error.code` 为 `UNSUPPORTED_CAPABILITY`

#### Scenario: 未知标的

- **WHEN** 上游返回 `1002` 且 message 含 `Unknown thscode`
- **THEN** HTTP 404，`error.code` 为 `INSTRUMENT_NOT_FOUND`

#### Scenario: 上游不可用

- **WHEN** 上游返回 5003 或连接失败
- **THEN** HTTP 502，`error.code` 为 `UPSTREAM_UNAVAILABLE`

### Requirement: 探测端点如实上报能力

系统 SHALL 提供 `GET /api/v1/market-data/sources/ths/probe`，返回
`status`（online/degraded/offline）、`checkedAt`、`latencyMs` 与 `capabilities`。
`capabilities.bars.periods` **SHALL 只含 `daily`**，且 SHALL NOT 声明
`liveBars`/`timeShare`/`timeShareRange`/`depth`/`marketTicks`。
未配置 key 时 SHALL 返回 `degraded` 而非崩溃。

#### Scenario: 上游可达

- **WHEN** 已配置有效 key 且上游可达
- **THEN** `data.status` 为 `online`，`capabilities.bars.periods` 为 `["daily"]`
- **THEN** 响应不含任何未实现能力的字段

#### Scenario: 未配置 key

- **WHEN** 未提供 `THS_API_KEY` 且 config.json 无 `ths.api_key`
- **THEN** `data.status` 为 `degraded`，HTTP 状态码仍为 200

### Requirement: 品种检索映射为协议描述符

系统 SHALL 提供 `POST /api/v1/market-data/instruments/search`，把上游
`asset_type` 映射为 KCQ `AssetClass`（`a-share`→`stock`、`a-share-index`→`index`、
`fund-etf`→`etf`、`fund-otc`→`fund`、`futures`→`future`、`options`→`option`，
其余→`unknown`），且描述符 SHALL 携带
`providerRef={thscode, assetType}` 作为唯一路由依据。

空 `keyword` SHALL 直接返回空列表而不请求上游（上游 `q` 必填）。

#### Scenario: 关键字命中

- **WHEN** 关键字命中标的
- **THEN** 返回描述符列表，`id` 形如 `ths:600519.SH`，`providerRef` 含 `thscode` 与 `assetType`

#### Scenario: 类别过滤

- **WHEN** 请求携带 `assetClasses`
- **THEN** 上游不支持该过滤，连接器在本地按映射后的 `assetClass` 过滤

#### Scenario: 空关键字

- **WHEN** `keyword` 为空字符串
- **THEN** 返回 `{"items": []}` 且不发起上游请求

### Requirement: K 线端点按域路由并如实声明量纲

系统 SHALL 提供 `POST /api/v1/market-data/bars`，按 `providerRef.assetType`
路由到对应上游端点，并 SHALL 显式返回 `volumeUnit`
（`a-share`/`a-share-index`/`fund-etf` → `share`；`futures`/`options` → `contract`）
与 `timezone`（恒为 `Asia/Shanghai`）。

显式返回 `volumeUnit` 是必须的：KCQ 内置兜底会把 `sessionId=='CN'` 且非指数的品种
判定为 `lot`（手），而同花顺 A 股为「股」，不覆盖将使成交量被放大 100 倍且图面无异常。

#### Scenario: 五种资产类型各自路由

- **WHEN** `assetType` 分别为 `a-share`/`a-share-index`/`fund-etf`/`futures`/`options`
- **THEN** 分别请求 `/api/a-share/prices/historical`、`/api/a-share-index/prices/historical`、
  `/api/fund/market/historical`、`/api/futures/prices/daily`、`/api/options/prices/daily`

#### Scenario: 时间字段两种形态

- **WHEN** 上游为 A股/指数/ETF
- **THEN** 从 `date_ms` 解析时间戳
- **WHEN** 上游为期货/期权
- **THEN** 从 `timestamp` 解析时间戳

#### Scenario: 周期参数名因域而异

- **WHEN** 请求 A股/指数
- **THEN** 必须显式传 `interval=1d`（缺失时上游返回 1001）
- **WHEN** 请求 ETF
- **THEN** 显式传 `interval=1d`
- **WHEN** 请求期货/期权
- **THEN** 显式传 `time_period=day_1`

#### Scenario: 复权仅 A 股支持

- **WHEN** `adjustment` 为 `qfq`/`hfq` 且 `assetType` 为 `a-share`
- **THEN** 传上游 `adjust=forward`/`backward`，并回传请求的 `adjustment`
- **WHEN** `adjustment` 非 `none` 且 `assetType` 非 `a-share`
- **THEN** 返回 400 `UNSUPPORTED_CAPABILITY`（不忽略参数返回未复权数据）
- **WHEN** `adjustment` 为 `splits`
- **THEN** 返回 400 `UNSUPPORTED_CAPABILITY`

#### Scenario: 聚合口径一律接受

- **WHEN** `barAggregation` 为 `original`/`aligned`/`europe-traditional`
- **THEN** 均接受并在响应中原样回传（同花顺日线本身即上海零点边界）

### Requirement: 分时端点明确声明不支持

系统 SHALL 提供 `POST /api/v1/market-data/timeshare` 并返回 400
`UNSUPPORTED_CAPABILITY`，而非静默返回空数据。

#### Scenario: 分时请求

- **WHEN** 调用分时端点
- **THEN** HTTP 400，`error.code` 为 `UNSUPPORTED_CAPABILITY`，message 说明上游未开放

### Requirement: 交易日历提供未来槽位

系统 SHALL 提供 `POST /api/v1/market-data/trading-calendar`，返回
`anchorTimestamp`（原样回传）与 `futureTimestamps`（anchor 之后的交易日毫秒戳，升序，
最多 `count` 个）。

#### Scenario: 取 anchor 之后的交易日

- **WHEN** 传入 `anchorTimestamp` 与 `count`
- **THEN** `futureTimestamps` 仅含严格大于 anchor 的交易日，且数量不超过 `count`

### Requirement: 跨源访问开放 CORS

系统 SHALL 允许任意来源的跨域请求（`allow_origins=["*"]`），供本地前端（如 5175）直连。

#### Scenario: 带 Origin 的请求

- **WHEN** 请求携带 `Origin` 头
- **THEN** 响应含 `access-control-allow-origin: *`
