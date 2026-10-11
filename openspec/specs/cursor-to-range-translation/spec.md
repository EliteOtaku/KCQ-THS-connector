# Cursor to Range Translation Specification

## Purpose

规定 KCQ 的游标分页（`limit` + 排他 `beforeTimestamp`）与同花顺区间查询
（`[start, end]` 闭区间，跨度上限 10 年）之间的翻译规则，以及
`olderData` 的判定。这是本连接器最易出错的部分，本规格是行为基准。

## Requirements

### Requirement: 排他游标翻译

系统 SHALL 把排他游标 `beforeTimestamp` 翻译为上游区间的闭区间上界
`end = beforeTimestamp - 1`；未提供游标时 SHALL 取 `end = 当前 UTC 毫秒`。

理由：上游区间**含两端**，若直接把游标当作 `end`，翻页时会把已返回的最后一根
K 线重复取回，导致分页错位。

#### Scenario: 不传游标

- **WHEN** 请求未提供 `beforeTimestamp`
- **THEN** 上游 `end` 等于当前毫秒，返回最新一页

#### Scenario: 传游标

- **WHEN** 请求提供 `beforeTimestamp = T`
- **THEN** 上游 `end` 等于 `T - 1`

### Requirement: 窗口天数按周期与条数反推

系统 SHALL 以 `window_days = min(ceil(limit * 1.6) + 10, 3650)` 计算回溯窗口，
并令 `start = end - window_days * 86400000`。

系数依据：A 股约 242 交易日/年 → 1 个交易日 ≈ 1.51 自然日，取 1.6 倍覆盖周末与节假日，
再留 10 天余量；上限 3650 天对应上游 10 年硬限。

#### Scenario: 常规条数

- **WHEN** `limit = 250`
- **THEN** `window_days` 为 `ceil(250 * 1.6) + 10`，即 410
- **THEN** `end - start` 等于 `410 * 86400000`

#### Scenario: 超出十年上限

- **WHEN** `limit` 大到使窗口超过 3650 天
- **THEN** `window_days` 被截为 3650，且标记已顶到上限

### Requirement: 必须显式传递区间参数

系统 SHALL 在每个上游历史 K 线请求中显式传递 `start` 与 `end`，
SHALL NOT 依赖上游省略参数时的默认行为。

理由：期货/期权端点省略 `start`/`end` 时上游返回"最近约 100 根"的隐式窗口，
依赖它做翻页会与 `limit` 语义冲突并错位。

#### Scenario: 期货请求

- **WHEN** 请求期货日线
- **THEN** 上游请求参数同时包含 `start` 与 `end`

### Requirement: olderData 判定

系统 SHALL 按以下规则返回 `olderData`：
若窗口已顶到上游 10 年上限，或本次返回条数少于 `limit`，则为 `exhausted`；
否则为 `available`。

#### Scenario: 顶到上限

- **WHEN** 窗口被截为 3650 天
- **THEN** `olderData` 为 `exhausted`

#### Scenario: 本页不足一页

- **WHEN** 返回条数小于 `limit`（如次新股或接近 10 年边界）
- **THEN** `olderData` 为 `exhausted`

#### Scenario: 满页且未顶上限

- **WHEN** 返回条数等于 `limit` 且窗口未顶上限
- **THEN** `olderData` 为 `available`

### Requirement: 只返回窗口内最后 limit 根

系统 SHALL 在上游返回结果中截取时间升序的最后 `limit` 根，
SHALL NOT 返回窗口内全部数据。

理由：窗口按 1.6 倍系数放宽以覆盖非交易日，必然多取；不截断会使前端收到超过
`limit` 的条数。

#### Scenario: 截断

- **WHEN** 窗口内上游返回 50 根而 `limit` 为 10
- **THEN** 响应 `items` 恰为最后 10 根，且按 `timestamp` 升序
