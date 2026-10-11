# KCQ-THS-connector

KCQ 的同花顺（fuyao）行情连接器：把 [同花顺金融数据 API](https://fuyao.aicubes.cn)
适配成 [KCQ-NexusAI](https://github.com/EliteOtaku/KCQ-NexusAI) 的
`market-data-v1` 协议，供图表引擎与 Agent 工具链消费。

> **能力边界（重要）**：同花顺**端外（`X-api-key`）只提供日线级行情**。
> A 股分钟线 / 分时「未开放外部接入」，期货与期权端外仅有日线。
> 因此本连接器**只声明并只提供 `daily`**。需要分钟级请使用
> [KCQ-MT5-connector](https://github.com/EliteOtaku/KCQ-MT5-connector)（8090）
> 或 GoTDX（8080）。这不是实现取舍，是上游数据可得性的硬约束。

## 架构

```
同花顺 fuyao.aicubes.cn (REST, X-api-key, 仅日线)
        │  httpx.AsyncClient 连接池 + 指数退避（禁止立即重试）
        ▼
FastAPI（app/routes.py，127.0.0.1:8092）
  ├─ GET  /api/v1/market-data/sources/ths/probe      探测 + 能力声明（只声明 daily）
  ├─ POST /api/v1/market-data/instruments/search     品种目录检索
  ├─ POST /api/v1/market-data/bars                   日线（游标→区间翻译）
  ├─ POST /api/v1/market-data/trading-calendar       未来交易日槽位
  └─ GET  /health/live | /health/ready               健康检查
        │
        ▼
KCQ-NexusAI core 数据层（SourceRouter → ths Provider）
```

- **域路由**：按 `asset_type` 分派到 A股 / 指数 / ETF / 期货 / 期权五个端点。
  域**不可混用**（ETF 打到 A股端点会返回 `1002 Unknown thscode`）。
- **游标翻译**：KCQ 用 `limit` + 排他游标 `beforeTimestamp`；同花顺用 `[start, end]`
  区间（上限 10 年）。翻译规则见 `openspec/specs/cursor-to-range-translation/`。
- **成交量单位**：同花顺 A股/指数/ETF 为**股**，期货/期权为**手**；连接器显式返回
  `volumeUnit` 覆盖 KCQ 的内置兜底（否则 A 股成交量会被按"手"解读，放大 100 倍）。

规范文档：`docs/UPSTREAM_CONTRACT.md`（上游实测契约）、`docs/BUILD_SPEC.md`（实现规格）、
`openspec/`（OpenSpec 规格）。

## 前提

- Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)
- 同花顺 API Key（在 [API Key 管理](https://fuyao.aicubes.cn/admin/) 页签发）

## 启动

```bash
uv run python ./server.py          # 默认 http://127.0.0.1:8092
```

KCQ-NexusAI 侧：数据源面板选择 `THS`，或运行时配置 baseUrl。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `THS_API_KEY` | 无 | 同花顺 API Key；未配置时 probe 返回 `degraded` |
| `THS_BASE_URL` | `https://fuyao.aicubes.cn` | 上游地址 |
| `THS_PORT` | `8092` | HTTP 端口 |
| `THS_TIMEOUT_S` | `20.0` | 单次上游请求超时（秒） |
| `THS_MAX_RETRIES` | `2` | 可重试错误的最大重试次数 |
| `THS_RETRY_BACKOFF_S` | `1.0` | 退避基数，实际等待 = base × 2^attempt |

Key 解析顺序：`THS_API_KEY` 环境变量 → `~/.cloudtrade/config.json` 的 `ths.api_key`。

## 测试

```bash
uv run pytest            # 单元/路由测试，FakeUpstream 替身，不访问网络
```

```bash
npx @fission-ai/openspec validate --all --strict   # 校验 OpenSpec 规格
```

真实上游冒烟（需配置 key）：`scripts/smoke_ths.ps1`

## License

[MIT](LICENSE)
