# BUILD SPEC — KCQ-THS-connector 实现规格

- 状态：待执行
- 读者：执行员
- **必读顺序**：本文 → `docs/UPSTREAM_CONTRACT.md`（上游实测契约）
  → KCQ 侧 `D:\AI\KCQ-NexusAI\packages\core\src\data\provider\protocol\types.ts`

> 本文是实现契约。**数值、字段名、行为均不得自行改动或"优化"**。
> 遇到本文与 `UPSTREAM_CONTRACT.md` 冲突 → 停下提问，不要猜。

---

## 0. 目标

在 `D:\AI\KCQ-THS-connector` 实现一个 FastAPI 服务，把同花顺 fuyao REST API
适配成 KCQ 的 `market-data-v1` 协议，**只支持日线**。端口 **8092**。

参照工程约定蓝本：`D:\AI\KCQ-MT5-connector`（先读它的 `AGENTS.md` + `app/config.py`
+ `app/gateway.py` + `tests/conftest.py`，照抄其风格）。

---

## 1. 文件清单（全部要建）

```
D:\AI\KCQ-THS-connector\
├── pyproject.toml
├── server.py                 # 启动入口
├── README.md
├── AGENTS.md
├── .gitignore
├── LICENSE                   # MIT
├── app\
│   ├── __init__.py
│   ├── config.py             # env → Settings（唯一参数来源）
│   ├── errors.py             # 错误码常量 + 分类
│   ├── envelope.py           # KCQ 信封封装/解包
│   ├── upstream.py           # httpx 唯一出口
│   ├── catalog.py            # 品种检索/代码表
│   ├── bars.py               # 游标→区间翻译 + 域路由 + 解析
│   ├── calendar.py           # 交易日历
│   ├── routes.py             # FastAPI 路由
│   └── main.py               # 装配
├── tests\
│   ├── conftest.py           # FakeUpstream（禁网络）
│   ├── test_errors.py
│   ├── test_envelope.py
│   ├── test_cursor.py        # 行为基准，改实现不得改断言
│   ├── test_bars.py
│   ├── test_catalog.py
│   └── test_routes.py
├── openspec\
│   ├── config.yaml
│   └── specs\
│       ├── v1-market-data-rest\spec.md
│       ├── ths-capability-declaration\spec.md
│       └── cursor-to-range-translation\spec.md
└── scripts\
    └── smoke_ths.ps1         # 真实 key 冒烟（人工执行）
```

（`docs/UPSTREAM_CONTRACT.md` 已存在，不要覆盖。）

---

## 2. pyproject.toml

```toml
[project]
name = "kcq-ths-connector"
version = "0.1.0"
description = "KCQ 同花顺(fuyao)日线行情连接器，实现 KCQ market-data V1 协议"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = ["fastapi>=0.115", "uvicorn>=0.30", "httpx>=0.27"]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.uv]
package = false
```

**不引入 pandas**（MT5 用了，但本连接器不需要）。

---

## 3. `app/config.py`

env → Settings，**禁止其他模块散落 `os.environ`**：

| env | 默认 | 说明 |
|---|---|---|
| `THS_API_KEY` | 无（必填） | 同花顺 key；缺失时启动不报错，但 probe 返回 degraded |
| `THS_BASE_URL` | `https://fuyao.aicubes.cn` | 上游地址 |
| `THS_PORT` | `8092` | 服务端口 |
| `THS_TIMEOUT_S` | `20.0` | 单次上游请求超时 |
| `THS_MAX_RETRIES` | `2` | 可重试错误的最大重试次数（首次之外） |
| `THS_RETRY_BACKOFF_S` | `1.0` | 退避基数，实际等待 = base * 2^attempt（**禁止立即重试**） |

key 解析顺序：`THS_API_KEY` env → `~/.cloudtrade/config.json` 的 `ths.api_key`。
**不得硬编码 key，不得写入 git。**

---

## 4. `app/errors.py`

```python
# 上游业务码（实测，含文档未记载的 2004）
CODE_OK = 0
CODE_MISSING_PARAM = 1001
CODE_BAD_PARAM = 1002          # 歧义：也可能是"未知标的"
CODE_OUT_OF_RANGE = 1003
CODE_PARAM_CONFLICT = 1004
CODE_UNAUTHENTICATED = 2001
CODE_FORBIDDEN = 2003
CODE_CLIENT_ONLY = 2004        # 同花顺AI客户端专用（文档未记载）
CODE_TARGET_NOT_FOUND = 3001
CODE_DATA_NOT_READY = 3002
CODE_ASSET_TYPE_UNSUPPORTED = 3004
CODE_RATE_LIMITED = 4001
CODE_INTERNAL = 5001
CODE_UPSTREAM_TIMEOUT = 5002
CODE_UPSTREAM_UNAVAILABLE = 5003

# KCQ MarketDataErrorCode 全集（provider/types.ts:331）——只有这 8 个，不得发明新值
KCQ_CODES = {
  "INVALID_REQUEST", "UNSUPPORTED_CAPABILITY", "INSTRUMENT_NOT_FOUND",
  "UPSTREAM_UNAVAILABLE", "TIMEOUT", "ABORTED", "INVALID_RESPONSE", "UNKNOWN",
}
# 仅这两个触发 KCQ 源流转（SOURCE_REJECTION_CODES）
ROUTABLE_CODES = {"UNSUPPORTED_CAPABILITY", "INSTRUMENT_NOT_FOUND"}
```

### 4.1 分类函数（**核心，必须严格实现**）

```python
def classify(code: int, message: str) -> tuple[str, bool]:
    """返回 (kcq_code, retryable)。"""
```

规则（**逐条对应，不得合并简化**）：

| 条件 | 返回 |
|---|---|
| `code == 1002` **且** `"Unknown thscode" in message` | `("INSTRUMENT_NOT_FOUND", False)` |
| `code == 1002`（其他） | `("INVALID_REQUEST", False)` |
| `code in (1001, 1003, 1004)` | `("INVALID_REQUEST", False)` |
| `code in (2001, 2003)` | `("UNKNOWN", False)` |
| `code == 2004` | `("UNSUPPORTED_CAPABILITY", False)` |
| `code == 3001` | `("INSTRUMENT_NOT_FOUND", False)` |
| `code == 3004` | `("UNSUPPORTED_CAPABILITY", False)` |
| `code == 4001` | `("UPSTREAM_UNAVAILABLE", True)` |
| `code == 5001` | `("UPSTREAM_UNAVAILABLE", True)` |
| `code == 5002` | `("TIMEOUT", True)` |
| `code == 5003` | `("UPSTREAM_UNAVAILABLE", True)` |
| HTTP 429 | `("UPSTREAM_UNAVAILABLE", True)` |
| 响应无法解析 | `("INVALID_RESPONSE", False)` |
| 其他未知码 | `("UNKNOWN", False)` |

> `1002` 的歧义消解是**必测点**：把「未知标的」误判为 `INVALID_REQUEST` 会让
> KCQ 不流转到下一个源，用户看到硬失败；反之会静默流转掩盖 bug。

`3002`（数据未就绪）**不进分类函数**——它是"合法无数据"，按 `code=0` + 空 items 处理。

---

## 5. `app/envelope.py`

KCQ 侧信封字段是 **camelCase**（`ProtocolEnvelope`），**不是**上游的 snake_case：

```python
def ok(data) -> dict:      # {"data": data, "requestId": <hex32>}
def fail(code, message, details=None) -> dict:
                           # {"error": {"code":..., "message":..., "details":...}, "requestId": ...}
```

- `requestId`：32 位十六进制字符串（`uuid4().hex`）。
- **上游的 `request_id` 只用于日志，不回传给 KCQ。**
- `fail` 的 `code` 必须是 §4 的 `KCQ_CODES` 之一。

另提供上游信封解包：

```python
class UpstreamEnvelope:  # 解析 {"code","message","request_id","data"}
    code: int; message: str; data: dict | None; request_id: str
def parse_upstream(raw: dict) -> UpstreamEnvelope   # 结构非法 → INVALID_RESPONSE
```

---

## 6. `app/upstream.py`（httpx **唯一出口**）

**硬约束（照抄 MT5 connector 的 gateway 原则）：除本模块外，任何模块禁止 import httpx。**

```python
class ThsUpstream:
    def __init__(self, settings): ...
    async def get(self, path: str, params: dict) -> UpstreamEnvelope:
        """发请求 + 解包 + 分类。业务错误抛 ThsApiError。"""
    async def aclose(self) -> None: ...

class ThsApiError(Exception):
    kcq_code: str      # KCQ_CODES 之一
    retryable: bool
    upstream_code: int | None
    message: str
```

行为要求：

1. 请求头带 `X-api-key`。
2. **429 / 可重试错误**：最多重试 `THS_MAX_RETRIES` 次，等待 `backoff * 2**attempt`。
   **禁止立即连续重试**（官方文档明确要求）。
3. 超时 → `TIMEOUT`（retryable）。
4. 非 JSON / 缺 `code` 字段 → `INVALID_RESPONSE`。
5. `code != 0` → 经 `classify()` 抛 `ThsApiError`。
6. 用单个复用的 `httpx.AsyncClient`（连接池），进程退出时关闭。

---

## 7. `app/catalog.py`

### 7.1 检索

`search(source_id, keyword, limit, asset_classes)` →
`GET /api/meta/tickers/search?q=<keyword>&limit=<limit>`

- 上游 `q` 必填；`keyword` 为空时**直接返回空列表**，不要打上游（避免 1001）。
- 上游不支持按 `asset_classes` 过滤 → 取回后**在本地过滤**（见 §7.3）。

`list_tickers(asset_type, limit, offset)` →
`GET /api/meta/tickers/list?asset_type=…&limit=…&offset=…`

### 7.2 descriptor 构造（**字段必须齐全**）

```python
{
  "id": f"ths:{thscode}",          # 例 "ths:600519.SH"
  "sourceId": "ths",
  "symbol": thscode,               # 用完整 thscode（含后缀）
  "name": name,
  "assetClass": <见 §7.3>,
  "exchange": exchange or "",      # 上游可能为 null → 用 ""
  "sessionId": <见 §7.4>,
  "currency": currency or None,
  "providerRef": {"thscode": thscode, "assetType": asset_type},
  "capabilities": {"bars": {"periods": ["daily"], "adjustments": <见 §7.5>}},
}
```

`providerRef` 是**唯一路由依据**（KCQ 原样带回），必须带 `thscode` 与 `assetType`。

### 7.3 `asset_type` → KCQ `AssetClass`（**KCQ 无 `fund-otc`**）

| 上游 | KCQ |
|---|---|
| `a-share` | `stock` |
| `a-share-index` | `index` |
| `fund-etf` | `etf` |
| `fund-otc` | `fund` |
| `futures` | `future` |
| `options` | `option` |
| 其他 | `unknown` |

KCQ 合法值仅：`stock|index|fund|etf|future|option|forex|crypto|unknown`。
**写错会被 `isFilterableAssetClass` 静默过滤掉。**

### 7.4 `sessionId`

- `stock` / `index` / `etf` → `"CN"`（KCQ 已内置该会话，**不要新增 marketSessions**）
- `future` / `option` / 其他 → **不设该字段**（`None`，其夜盘时段与股票不同）

### 7.5 `adjustments`（每品种能力）

- `stock`（a-share）→ `["none", "qfq", "hfq"]`
- 其他全部 → `["none"]`（指数/ETF/期货/期权无复权概念）

---

## 8. `app/bars.py`（**最关键**）

### 8.1 前置校验（顺序固定，先校验后请求）

| 条件 | 行为 |
|---|---|
| `period != "daily"` | 抛 `ThsApiError("UNSUPPORTED_CAPABILITY")` |
| `adjustment == "splits"` | `UNSUPPORTED_CAPABILITY` |
| `adjustment != "none"` 且 `assetType != "a-share"` | `UNSUPPORTED_CAPABILITY` |
| 无法从 `providerRef` / `symbol` 解析出 thscode | `INVALID_REQUEST` |

`barAggregation`（`original`/`aligned`/`europe-traditional`）**一律接受**：
同花顺日线本身即 Asia/Shanghai 零点边界，三种口径结果相同。在响应中原样回传请求值。

### 8.2 游标 → 区间翻译（**行为基准，必须逐字实现**）

KCQ 是 `limit` + 排他游标 `beforeTimestamp`；上游是 `[start, end]` 区间：

```python
DAY_MS = 86_400_000
MAX_WINDOW_DAYS = 3650          # 上游硬限 10 年（>10年 → 1003）

def resolve_window(limit: int, before_timestamp: int | None, now_ms: int):
    end = (before_timestamp - 1) if before_timestamp is not None else now_ms
    # A股约 242 交易日/年 → 1 个交易日 ≈ 1.51 自然日；取 1.6 倍 + 10 天余量
    window_days = min(ceil(limit * 1.6) + 10, MAX_WINDOW_DAYS)
    start = end - window_days * DAY_MS
    capped = window_days >= MAX_WINDOW_DAYS
    return start, end, capped
```

**`end = before_timestamp - 1` 是排他语义**（上游区间含两端）。
不传游标 → `end = now_ms`（返回最新一页）。

### 8.3 取数后处理

```python
items = parsed_bars[-limit:]        # 取窗口内最后 limit 根（升序）
older_data = "exhausted" if (capped or len(items) < limit) else "available"
```

- `capped`：已顶到上游 10 年上限 → 更早的拿不到 → `exhausted`
- `len(items) < limit`：确实没有更多历史（如次新股）→ `exhausted`
- 否则 → `available`

### 8.4 域路由（**不可混用，混用会 1002 Unknown thscode**）

| assetType | 端点 | 时间字段 | adjust 参数 |
|---|---|---|---|
| `a-share` | `/api/a-share/prices/historical` | **`date_ms`** | 传 |
| `a-share-index` | `/api/a-share-index/prices/historical` | **`date_ms`** | 不传 |
| `fund-etf` | `/api/fund/market/historical` | **`date_ms`** | 不传 |
| `futures` | `/api/futures/prices/daily` | **`timestamp`** | 不传 |
| `options` | `/api/options/prices/daily` | **`timestamp`** | 不传 |

- **必须显式传 `start`/`end`**：期货/期权省略时会返回"最近约 100 根"（上游隐式窗口），
  依赖它翻页会错位。
- `date_ms` 与 `timestamp` **都要解析**（字段名不一致是硬事实）。
- `adjust` 映射：`none→none`、`qfq→forward`、`hfq→backward`。
  **必须显式传**（上游默认 `forward`，不传会让 `none` 请求静默拿到前复权数据）。
- 指数/ETF/期货/期权**不传** `adjust`。

### 8.5 成交量单位（**静默错误，最易错**）

按 `assetType` 显式返回 `volumeUnit`（**实测值，见 UPSTREAM_CONTRACT §4**）：

| assetType | volumeUnit | 实测依据 |
|---|---|---|
| `a-share` | `share` | 2516757 × 1255.79 ≈ turnover（比 0.995） |
| `a-share-index` | `share` | 与 turnover 同量纲 |
| `fund-etf` | `share` | 629177440 × 4.552 ≈ turnover ✓ |
| `futures` | `contract` | 90192 × 7122.8 × 乘数200 ≈ turnover（比 1.011） |
| `options` | `contract` | 654 × 0.1336 × 10000 ≈ turnover |

**必须显式返回**：KCQ 内置兜底会把 `sessionId=='CN'` 且非指数的品种当 `lot`（手），
而同花顺 A 股是**股** → 不覆盖会**放大 100 倍且图形无异常**。

### 8.6 响应构造

```python
{
  "instrumentId": instrument_id,       # 原样回传请求的 instrument.id
  "period": "daily",
  "adjustment": <原样回传>,
  "barAggregation": <原样回传>,
  "timezone": "Asia/Shanghai",         # 恒为上海时区
  "volumeUnit": <见 §8.5>,
  "items": [{"timestamp":…, "open":…, "high":…, "low":…, "close":…,
             "volume":…, "turnover":…}, …],   # 按 timestamp 升序
  "olderData": <见 §8.3>,
}
```

字段名必须是 KCQ 的 `ProtocolKLineItem`：`timestamp/open/high/low/close/volume/turnover`
（**不是**上游的 `open_price` 等）。`timestamp` 直接透传上游值，**不做时区换算**。

---

## 9. `app/calendar.py`

`GET /api/a-share/calendar/trading-days`（无参数，固定近一年）→

```python
{"anchorTimestamp": <请求原样回传>,
 "futureTimestamps": [<anchor 之后的交易日毫秒戳，升序>]}
```

---

## 10. `app/routes.py`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/market-data/sources/{source_id}/probe` | 探测 |
| POST | `/api/v1/market-data/instruments/search` | 检索 |
| POST | `/api/v1/market-data/bars` | K 线 |
| POST | `/api/v1/market-data/trading-calendar` | 交易日历 |
| POST | `/api/v1/market-data/timeshare` | **P0 未实现** → 返回 `UNSUPPORTED_CAPABILITY` 错误信封 |
| GET | `/health/live` | 存活 |
| GET | `/health/ready` | 就绪 |

- `source_id` 非 `"ths"` → 404。
- 成功响应走 `ok()`；错误走 `fail()`，**HTTP 状态码必须是非 2xx**（见下）。
  `ThsApiError` 用异常处理器统一转 `fail()`。
- 全局开启 CORS（`allow_origins=["*"]`），与 GoTDX/MT5 一致，供 5175 直连。

### 10.0 错误响应的 HTTP 状态码（**必须非 2xx，否则源流转失效**）

KCQ 的 `httpTransport.ts:86-99` **只在 `!res.ok` 分支解析错误信封**；
2xx 时它要求 body 含 `data`，否则抛通用 `FETCH_FAILED` 并**丢弃错误码** ——
`UNSUPPORTED_CAPABILITY` / `INSTRUMENT_NOT_FOUND` 因此无法触发 `SourceRouter` 流转。

映射（与 MT5 connector、GoTDX 的既有约定一致）：

| KCQ 码 | HTTP |
|---|---|
| `INVALID_REQUEST` | 400 |
| `UNSUPPORTED_CAPABILITY` | 400 |
| `INSTRUMENT_NOT_FOUND` | 404 |
| `UPSTREAM_UNAVAILABLE` | 502 |
| `TIMEOUT` | 504 |
| `INVALID_RESPONSE` | 502 |
| `ABORTED` | 499 |
| `UNKNOWN` | 500 |

> 注意：这是**连接器对外**的 HTTP 语义，与上游同花顺"业务错误恒 HTTP 200"是两回事
> ——上游那层由 `app/upstream.py` 消化，不能泄漏到 KCQ 侧。

### 10.1 probe 响应（**能力必须写实**）

```json
{"data": {
  "status": "online",
  "checkedAt": 1791716283000,
  "latencyMs": 42,
  "capabilities": {
    "assetClasses": ["stock","index","etf","fund","future","option"],
    "bars": {"periods": ["daily"], "adjustments": ["none","qfq","hfq"]},
    "tradingCalendar": true
  }
}, "requestId": "…"}
```

- `status`：`online` / `degraded`（未配 key 或无网）/ `offline`。
  探测方式：轻量调 `meta/tickers/search?q=…` 或 `calendar/trading-days`。
- **`periods` 只能有 `daily`**。**不得声明** `liveBars` / `timeShare` /
  `timeShareRange` / `depth` / `marketTicks`。
- 写实是**功能**不是保守：KCQ `SourceRouter` 会据此在请求 `1min` 时自动流转到
  MT5/gotdx，无需任何特判。

---

## 11. 测试（**禁网络**）

`tests/conftest.py` 提供 `FakeUpstream`（照抄 MT5 connector 的 FakeGateway 思路），
替换 `app.upstream.ThsUpstream`，按 path+params 返回预置信封。

必须覆盖：

**`test_errors.py`**
- `1002` + `"Unknown thscode: 999999.SH"` → `INSTRUMENT_NOT_FOUND`（**关键**）
- `1002` + `"Invalid parameter format: interval"` → `INVALID_REQUEST`
- `2004` → `UNSUPPORTED_CAPABILITY`
- `3001` → `INSTRUMENT_NOT_FOUND`；`3004` → `UNSUPPORTED_CAPABILITY`
- `4001`/`5001`/`5003` → `UPSTREAM_UNAVAILABLE` 且 retryable=True
- `5002` → `TIMEOUT` 且 retryable=True
- `2001`/`2003` → `UNKNOWN` 且 retryable=False
- 未知码 → `UNKNOWN`

**`test_cursor.py`**（行为基准，改实现不得改断言）
- 不传游标 → `end == now_ms`
- 传游标 → `end == before - 1`（排他）
- `limit=250` → `window_days == ceil(250*1.6)+10 == 410`
- 极大 limit → `window_days` 被截到 3650，且 `olderData == "exhausted"`
- 返回根数 < limit → `exhausted`；== limit → `available`

**`test_bars.py`**
- 五种 assetType 各自路由到正确端点（断言 path）
- `date_ms` 与 `timestamp` 两种响应都能解析出正确 `timestamp`
- `volumeUnit` 五种取值逐一断言（`share`×3 / `contract`×2）
- `adjust` 映射：`none→none`、`qfq→forward`、`hfq→backward`，且 A 股才传
- `splits` → `UNSUPPORTED_CAPABILITY`
- 指数 + `adjust=qfq` → `UNSUPPORTED_CAPABILITY`
- `period="1min"` → `UNSUPPORTED_CAPABILITY`
- 显式传 start/end（断言 params 里两者都在）

**`test_catalog.py`**
- `asset_type` 六种映射（含 `fund-otc → fund`）
- `providerRef` 含 `thscode` 与 `assetType`
- `sessionId=="CN"` for stock/index/etf；future/option 无该字段
- `adjustments` 按 §7.5

**`test_routes.py`**
- 4 个 V1 端点 + health 各 200
- 成功信封恰为 `{"data","requestId"}`
- 错误信封恰为 `{"error":{"code","message"},"requestId"}`
- probe 的 `periods == ["daily"]`
- `source_id != "ths"` → 404
- `timeshare` → `UNSUPPORTED_CAPABILITY`

**`test_envelope.py`**：`ok`/`fail` 结构与 `requestId` 格式。

---

## 12. openspec

照 `D:\AI\KCQ-MT5-connector\openspec` 的结构写 `config.yaml` + 三份 spec：

1. `v1-market-data-rest/spec.md`：5 端点 + 信封 + 错误映射 + CORS
2. `ths-capability-declaration/spec.md`：**只声明 daily** 的理由与硬约束；
   capability 是契约不是营销；不得声明未实现能力
3. `cursor-to-range-translation/spec.md`：§8.2/§8.3 的窗口与 `olderData` 规则

验收：`npx @fission-ai/openspec validate --all --strict` 全绿。

---

## 13. README.md / AGENTS.md

- `README.md`：架构图、启动方式、env 表、**明确写出「仅日线」与上游限制**、
  测试命令。风格照 `KCQ-MT5-connector\README.md`。
- `AGENTS.md`：职责边界（**本服务是同花顺域特异的唯一适配点**；下游禁止直连
  同花顺或自行做 `asset_type` 路由/量纲判断）、代码要求（httpx 只走 upstream.py、
  参数集中在 config.py、注释中文）、命令表、域不变量（§8.4/§8.5 的硬事实）。

---

## 14. 验收命令

```powershell
cd D:\AI\KCQ-THS-connector
uv sync
uv run pytest                     # 必须全绿，且不访问网络
npx @fission-ai/openspec validate --all --strict   # 必须全绿
uv run python ./server.py         # 监听 127.0.0.1:8092
```

真实 key 冒烟（`scripts/smoke_ths.ps1`，key 从 env/config 读，**不得硬编码**）：

```powershell
curl.exe 127.0.0.1:8092/api/v1/market-data/sources/ths/probe
# 期望：status=online，periods 仅含 daily
curl.exe -X POST 127.0.0.1:8092/api/v1/market-data/bars `
  -H 'Content-Type: application/json' `
  -d '{"sourceId":"ths","instrument":{"id":"ths:600519.SH","symbol":"600519.SH","exchange":"SH","providerRef":{"thscode":"600519.SH","assetType":"a-share"}},"period":"daily","adjustment":"none","barAggregation":"original","limit":30}'
# 期望：items 约 30 根，volumeUnit=="share"，timezone=="Asia/Shanghai"
```

---

## 15. 完成标准

- [ ] §1 全部文件存在
- [ ] `uv run pytest` 全绿，**无网络访问**
- [ ] `openspec validate --all --strict` 全绿
- [ ] `server.py` 能在 8092 启动
- [ ] 真实 key 冒烟：probe + bars 均 `code=0`
- [ ] 报告中明确列出：实际运行的命令与结果；**未验证项**

**不要 commit、不要 push**（用户明确要求先不提交）。
