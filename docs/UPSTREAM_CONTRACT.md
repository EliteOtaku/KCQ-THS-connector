# 同花顺上游契约（GROUND TRUTH）

- 版本：2026-10-11
- 状态：**已用真实 key 逐端点实测**，多处与官方文档不符，**本文以实测为准**
- 上游 Base URL：`https://fuyao.aicubes.cn`
- 鉴权：请求头 `X-api-key: <key>`

> **纪律**：实现时若本文与官方文档冲突，**以本文为准**（本文是实测结果）。
> 若发现本文与实测再次冲突，先改本文再改代码。

---

## 1. 响应信封

所有端点统一返回 HTTP 200 + 信封（**业务错误不用 HTTP 状态码表达**）：

```json
{ "code": 0, "message": "success", "request_id": "…", "data": { … } }
```

- `code == 0` 成功；非 0 为业务错误。
- `data` 在错误时可能为 `null`。
- 限流时可能返回 HTTP 429（此时不保证信封）。

## 2. 错误码（实测，含文档未记载的 2004）

| code | 含义 | 实测触发场景 | KCQ 映射 |
|---|---|---|---|
| `0` | 成功 | — | — |
| `1001` | 缺必填参数 | `meta/tickers/search` 无 `q` | `INVALID_REQUEST` |
| `1002` | 参数格式错误 **或 未知标的** | `interval=1m`；`thscode=999999.SH`；**跨域标的**（ETF 打到 A股端点） | 见 §2.1 |
| `1003` | 参数越界 | `[start,end]` 跨度 > 10 年 | `INVALID_REQUEST` |
| `1004` | 参数冲突 | — | `INVALID_REQUEST` |
| `2001` | 未认证 | key 缺失/无效 | `UNKNOWN`（非 retryable） |
| `2003` | 权限不足 | key 无权调用该 capability | `UNKNOWN` |
| **`2004`** | **同花顺AI客户端专用**（**文档未记载**） | `high-frequency/*`、`capital-flow/*`、`news/events/search` | **`UNSUPPORTED_CAPABILITY`**（触发源流转） |
| `3001` | 标的不存在 | **期货/期权**端点：`Target not found: CU2601.SHF` | `INSTRUMENT_NOT_FOUND`（触发流转） |
| `3002` | 数据未就绪 | — | 不报错，返回空 items |
| `3004` | 标的类型不支持该能力 | — | `UNSUPPORTED_CAPABILITY`（触发流转） |
| `4001` | 频率超限 | — | `UPSTREAM_UNAVAILABLE`（retryable） |
| `5001` | 服务内部错误 | — | `UPSTREAM_UNAVAILABLE`（retryable） |
| `5002` | 上游服务超时 | — | `TIMEOUT`（retryable） |
| `5003` | 数据源不可用 | — | `UPSTREAM_UNAVAILABLE`（retryable） |

### 2.1 `1002` 的歧义必须消解（关键）

`1002` 同时表示「参数格式错误」和「未知标的」。**必须读 `message` 区分**：

```python
if code == 1002 and "Unknown thscode" in message:
    → INSTRUMENT_NOT_FOUND   # 可触发源流转
else:
    → INVALID_REQUEST        # 不流转
```

**理由**：若把「未知标的」误判为 `INVALID_REQUEST`，KCQ 的 `SourceRouter` 不会流转到
下一个源，用户会看到硬失败而不是自动回退；反之把参数错误当标的不存在，会静默流转并掩盖 bug。

### 2.2 只有两个码触发源流转

KCQ `SOURCE_REJECTION_CODES = ['UNSUPPORTED_CAPABILITY', 'INSTRUMENT_NOT_FOUND']`。
本 connector 中对应：`2004` / `3004` / `3001` /（`1002`+`Unknown thscode`）。
**其余一律原样上抛**，不得自行吞掉或改写。

---

## 3. 路由表（asset_type → 端点）

**核心约束：域不可混用。** `510300.SH`（ETF）打到 `/api/a-share/prices/historical`
返回 `1002 Unknown thscode`。必须按 `asset_type` 路由。

| asset_type | 历史 K 线端点 | 时间字段 | 周期参数 | adjust 参数 | 快照端点 |
|---|---|---|---|---|---|
| `a-share` | `GET /api/a-share/prices/historical` | **`date_ms`** | `interval`=1d **必填** | 支持 | `GET /api/a-share/prices/snapshot` |
| `a-share-index` | `GET /api/a-share-index/prices/historical` | **`date_ms`** | `interval`=1d **必填** | 无（响应 `adjust` 恒 `null`） | `GET /api/a-share-index/prices/snapshot` |
| `fund-etf` | `GET /api/fund/market/historical` | **`date_ms`** | `interval`=1d 可选 | 无 | `GET /api/fund/market/snapshot` |
| `futures` | `GET /api/futures/prices/daily` | **`timestamp`** | `time_period`=day_1 | 无 | — |
| `options` | `GET /api/options/prices/daily` | **`timestamp`** | `time_period`=day_1 | 无 | — |

> **周期参数名与必填性因域而异（实测，易漏）**：
> A股与指数端点**必须传 `interval`**，缺失直接返回
> `1001 Missing required parameter: interval`；ETF 的 `interval` 可选；
> 期货/期权用 `time_period`（端外仅允许 `day_1`）。
> **一律显式传值**，不要依赖上游默认或缺省行为。

> **字段名不一致是硬事实**：A股/指数/ETF 用 `date_ms`，期货/期权用 `timestamp`。
> 解析器必须两条路径都支持，**不能只写一条**。

### 3.1 参数差异（同样不可混用）

| 端点 | start/end | 单标的约束 | 备注 |
|---|---|---|---|
| `a-share/prices/historical` | **必填** | 单 `thscode`，不接受逗号 | 窗口 ≤10 年 |
| `a-share-index/prices/historical` | **必填** | 单 `thscode` | 窗口 ≤10 年；无 `adjust` |
| `fund/market/historical` | **必填** | 单 `thscode` | — |
| `futures/prices/daily` | **可省** | 单 `thscode` | **省略时返回最近 101 根** |
| `options/prices/daily` | **可省** | 单 `thscode` | 省略时返回最近 100 根 |

**期货/期权省略 start/end 会返回"最近约 100 根"** —— 这不是分页，
是**上游隐式窗口**。connector 若依赖它做翻页会错位，**必须显式传 start/end**。

### 3.2 复权（只有 A 股支持）

| 上游 `adjust` | KCQ `KLineAdjustment` |
|---|---|
| `none` | `none` |
| `forward` | `qfq`（前复权） |
| `backward` | `hfq`（后复权） |
| — | `splits` → 不支持，返回 `UNSUPPORTED_CAPABILITY` |

- **上游默认 `forward`**。connector **必须显式传 `adjust`**，否则 KCQ 的 `none`
  请求会静默拿到前复权数据（**静默错误，图形上看不出来**）。
- 指数 / ETF / 期货 / 期权 **无复权概念**：请求 `adjust != none` 时应返回
  `UNSUPPORTED_CAPABILITY`，而不是忽略参数返回未复权数据。

---

## 4. 成交量量纲（实测验算，**文档未写明**）

**这是最容易出错且最隐蔽的地方** —— 单位错了图形完全正常，只有数值错。

| asset_type | `volume` 单位 | 验算证据 | KCQ `volumeUnit` |
|---|---|---|---|
| `a-share` | **股** | 600519: 2516757 × 1255.79 = 3.16e9 ≈ turnover 3.145e9（比 0.995） | `share` |
| `a-share-index` | **股/份** | 000001.SH: volume=5.35e10 与 turnover 同量纲 | `share` |
| `fund-etf` | **股/份** | 510300: 629177440 × 4.552 = 2.86e9 ≈ turnover 2.86e9 ✓ | `share` |
| `futures` | **手** | IC2612: 90192 × 7122.8 × 乘数200 = 1.285e11 ≈ turnover 1.299e11（比 1.011） | `contract` |
| `options` | **手（张）** | 10011425: 654 × 0.1336 × 10000 = 873k ≈ turnover 931k | `contract` |

### 4.1 KCQ 侧有个**错误的默认兜底**，必须覆盖

`packages/core/src/data/provider/protocol/impl/provider.ts` 内置：

```ts
// CN 会话且非指数 → 'lot'（手）
instrument.sessionId === 'CN' && instrument.assetClass !== 'index' ? 'lot' : undefined
```

而 THS 的 A 股 `volume` 是**股**。若 connector 不显式返回 `volumeUnit`，
A 股成交量会被下游按「手」解读 → **系统性放大 100 倍，且图形无异常**。

→ **connector 必须在 `ProtocolBarSeries.volumeUnit` 显式返回上表的值**，覆盖兜底。

---

## 5. 时间戳与时区

- 所有时间戳为**毫秒 Unix**。
- `date_ms` / `timestamp` 本身已是**真正的 epoch 毫秒**（Asia/Shanghai 零点对应值），
  **直接透传，不做任何换算**。
- 响应 `ProtocolBarSeries.timezone` 恒为 `"Asia/Shanghai"`。

验算：600519 的 `date_ms=1791388800000` → UTC `2026-10-07T16:00:00Z` →
Asia/Shanghai `2026-10-08T00:00:00+08:00` ✓（即 10-08 交易日零点）。

---

## 6. `sessionId` 映射

KCQ `MarketSessionRegistry` **已内置** `CN/HK/KR/US/MT5`：

- `CN` = `ASHARE_MARKET_SESSION`（`sessionTimeLabels.ts:59`）：
  `timeZone: 'Asia/Shanghai'`，`sessions: [09:30-11:30, 13:00-15:00]`，`slotMinutes: 1`
- 因此 **A 股 / 指数 / ETF 直接声明 `sessionId: 'CN'`，不需要在
  `sourceRegistry` 新增 `marketSessions`**（与 MT5 必须自定义 `MT5` 会话不同）。

**硬约束**：`chartDataManager.ts:465` 在**分时**路径强制要求 `sessionId` 非空
（缺则抛 `sessionId is required`）。本 connector P0 **不提供分时**，但品种描述
仍应带上 `sessionId`，避免下游其他路径踩到。

**期货 / 期权 P0 不给 `sessionId`**：其交易时段（含夜盘）与股票不同，
用 `CN` 会导致分时轴错位。留空即可。

---

## 7. 目录与检索

### 7.1 `GET /api/meta/tickers/search`（品种检索）

| 参数 | 必填 | 说明 |
|---|---|---|
| `q` | **是** | 关键词（缺 → `1001`） |
| `limit` | 否 | 返回条数 |

响应 `item[]`：`{thscode, ticker, name, exchange, asset_type, currency, list_date, …}`

实测 `asset_type` 值域：`a-share` / `a-share-index` / `futures` / `options` /
`fund-etf` / `fund-otc`

### 7.2 `GET /api/meta/tickers/list`（代码表分页）

| 参数 | 说明 |
|---|---|
| `asset_type` | 按类型过滤 |
| `limit` / `offset` | 分页 |

### 7.3 `asset_type` → KCQ `AssetClass` 映射

| 上游 `asset_type` | KCQ `AssetClass` |
|---|---|
| `a-share` | `stock` |
| `a-share-index` | `index` |
| `fund-etf` | `etf` |
| `fund-otc` | `fund` |
| `futures` | `future` |
| `options` | `option` |

> **KCQ `AssetClass` 无 `fund-otc` 对应项**（有 `fund`），故 `fund-otc` → `fund`。
> 注意 KCQ 的 `ASSET_CLASS_VALUES` 是
> `stock|index|fund|etf|future|option|forex|crypto|unknown`，
> **没有 `fund-otc` 这种值**，写错会被 `isFilterableAssetClass` 过滤掉。

---

## 8. 交易日历

`GET /api/a-share/calendar/trading-days`（**无参数**，固定返回近一年）

响应 `item[]`：`{date_ms, date}`，`date` 为 `yyyyMMdd`。

→ 用于实现 KCQ 的 `fetchTradingCalendar`（返回 anchor 之后的未来交易槽位）。

---

## 9. 真实标的样例（写测试与文档用）

| 类型 | thscode | 名称 |
|---|---|---|
| A股 | `600519.SH` | 贵州茅台 |
| A股 | `000001.SZ` | 平安银行 |
| 指数 | `000001.SH` | 上证综指 |
| 指数 | `000300.SH` | 沪深300 |
| 指数(TI) | `885525.TI` | 白酒概念 |
| ETF | `510300.SH` | 沪深300ETF华泰柏瑞 |
| 期货 | `IC2612.CFE` | 中证500 2612 |
| 期权 | `10011425.SH` | 50ETF购12月2800 |

> **注意**：文档示例里的 `IO2601-C-4000.CFE` 实测**不存在**（返回 3001）。
> 期权真实代码形如 `10011425.SH` / `90007464.SZ`（数字代码）。
> **不要在测试或文档里使用文档示例的期权代码。**

---

## 10. capability 实测范围（用户 key）

**已开通**：A股行情（含复权）、指数、ETF、期货、期权、meta 检索、交易日历、
复权因子、估值、基金

**未开通**（`2004`）：高频动向、主力资金、资讯事件

→ **日线方案完全可行**，覆盖面足够。

---

## 11. 限流

- 不限累计调用次数；QPS 动态限流。
- 触发时 HTTP 429 或 `code=4001`。
- 官方明确要求「**不要立即连续重试**」。
- **P0 不做缓存**（日线一天变一次，且不限累计次数）。实测触到 429 再加。
