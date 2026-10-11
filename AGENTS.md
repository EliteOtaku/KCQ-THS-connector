# KCQ-THS-connector — Agent Guide

KCQ 的同花顺（fuyao）日线行情连接器：httpx 访问
[同花顺金融数据 API](https://fuyao.aicubes.cn)，FastAPI 实现 KCQ `market-data-v1`
协议。消费方为同级仓库 KCQ-NexusAI 的 core 行情层。

## 职责边界（最重要——AI 编码前必读）

**本服务是同花顺域特异的唯一适配点。**

所有下游消费方（KCQ 指标引擎、绘图、Agent 工具、宿主脚本）只消费本服务输出的
已归一化数据，**禁止各自**：

- 直连 `fuyao.aicubes.cn` 或绕过本服务；
- 自行做 `asset_type` → 端点路由（A股/指数/ETF/期货/期权五域**不可混用**，
  ETF 打到 A股端点会返回 `1002 Unknown thscode`）；
- 自行判断成交量量纲（A股/指数/ETF 为**股**，期货/期权为**手**）；
- 自行做游标 → 区间翻译。

原因：同花顺的域路由、字段名差异（`date_ms` vs `timestamp`）、量纲差异与
错误码歧义（`1002` 既表示参数错误也表示未知标的）都是**上游特异性**缺陷。
若散落到 N 个消费方，每新增一个指标/消费方就要重复处理一次，且口径极易漂移。

**新增上游特异修正时，收敛在本服务内部实现，下游无感知。**

## 上游契约

**`docs/UPSTREAM_CONTRACT.md` 是上游真实行为的唯一真相源（实测所得，多处与
官方文档不符）。** 实现时若与官方文档冲突，以该文档为准。

## 代码要求

- 拒绝治标不治本的最小修复；不做复杂化架构。
- **httpx 只允许出现在 `app/upstream.py`**；其他模块禁止直接 import httpx。
- 全部可调参数集中在 `app/config.py`（env → Settings），禁止散落 `os.environ`。
- API Key 只从 `THS_API_KEY` env 或 `~/.cloudtrade/config.json` 读取，
  **禁止硬编码、禁止入库**。
- 注释正文中文、技术用语保留英文；每文件头部注释说明用途，每函数注释说明职责。

## Commands

| Command | What |
|---------|------|
| `uv sync` | 安装依赖（首次） |
| `uv run python ./server.py` | 启动连接器（:8092） |
| `uv run pytest` | 全量测试（FakeUpstream 替身，不访问网络） |
| `npx @fission-ai/openspec validate --all --strict` | 校验 OpenSpec 规格 |

## Testing

- **测试禁止访问网络**：`tests/conftest.py` 的 FakeUpstream 替换 `app.upstream.ThsUpstream`。
- 游标翻译用例为**行为基准**：`end = before - 1` 的排他语义、窗口天数、
  `olderData` 判定——**改实现不得改断言**。

## Domain Invariants

- **只提供 `daily`**：同花顺端外无分钟级行情，probe 的 `periods` 只能是 `["daily"]`。
  写实是功能：KCQ `SourceRouter` 据此在请求 `1min` 时自动流转到 MT5/gotdx。
- **`1002` 歧义**：必须读 message——含 `Unknown thscode` → `INSTRUMENT_NOT_FOUND`
  （可触发源流转）；否则 → `INVALID_REQUEST`（不流转）。
- **只有 `UNSUPPORTED_CAPABILITY` 与 `INSTRUMENT_NOT_FOUND` 触发源流转**，
  其余错误原样上抛。
- **域不可混用**：按 `asset_type` 路由到五个不同端点。
- **时间字段不一致**：A股/指数/ETF 用 `date_ms`，期货/期权用 `timestamp`。
- **量纲**：A股/指数/ETF=`share`，期货/期权=`contract`；**必须显式返回
  `volumeUnit`**，否则 KCQ 内置兜底会按"手"解读 A 股成交量，放大 100 倍且图形无异常。
- **复权**：只有 A 股支持；必须**显式传** `adjust`（上游默认 `forward`，
  不传会让 `none` 请求静默拿到前复权数据）。
- **时间戳直接透传**，不做时区换算；`timezone` 恒为 `Asia/Shanghai`。

## Committing

- Conventional Commits（无前缀）；一次提交只做一件事。
- 仅在用户明确要求时 commit/push。
