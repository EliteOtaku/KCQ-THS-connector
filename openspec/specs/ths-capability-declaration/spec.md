# THS Capability Declaration Specification

## Purpose

规定本连接器对 KCQ 声明能力的规则。能力声明是**契约**而非营销描述：
KCQ 的 `SourceRouter` 依据 probe 返回的 `capabilities` 在请求前筛选候选源，
声明与实现不符会污染图表与 Agent 结果，且难以察觉。

## Requirements

### Requirement: 只声明实际支持的能力

系统 SHALL 在 probe 响应中只声明实际实现的能力。
`capabilities.bars.periods` SHALL 恰为 `["daily"]`，SHALL NOT 包含任何分钟级或更高周期。

理由：同花顺端外只提供日线级行情——A 股分钟线/分时未开放外部接入（上游返回 `2004`），
期货与期权端外仅有日线。声明分钟级而实际返回日线属于**静默数据错误**。

#### Scenario: 周期声明

- **WHEN** 调用 probe
- **THEN** `capabilities.bars.periods` 等于 `["daily"]`

#### Scenario: 未实现能力不得出现

- **WHEN** 调用 probe
- **THEN** `capabilities` 不含 `liveBars`、`timeShare`、`timeShareRange`、`depth`、`marketTicks`

### Requirement: 写实声明驱动源流转而非特判

系统 SHALL 依赖 capability 写实声明使 KCQ 在请求不支持周期时自动流转到其它数据源，
SHALL NOT 通过返回伪造周期数据来"兼容"请求。

#### Scenario: 请求分钟级周期

- **WHEN** 客户端请求 `period=1min`
- **THEN** 返回 400 `UNSUPPORTED_CAPABILITY`（属 `SOURCE_REJECTION_CODES`），
  由 `SourceRouter` 流转到 MT5/gotdx 等支持该周期的源

### Requirement: 资产类别声明覆盖已实现域

系统 SHALL 声明 `assetClasses` 为 `["stock","index","etf","fund","future","option"]`，
即五种已路由的域加上 `fund-otc` 归一后的 `fund`。

#### Scenario: 资产类别

- **WHEN** 调用 probe
- **THEN** `capabilities.assetClasses` 覆盖上述六项

### Requirement: 品种级能力按资产类别区分复权

系统 SHALL 在品种描述符的 `capabilities.bars.adjustments` 中按资产类别声明复权能力：
`stock` 为 `["none","qfq","hfq"]`，其余为 `["none"]`。

#### Scenario: A 股可复权

- **WHEN** 描述符对应 `assetClass=stock`
- **THEN** `capabilities.bars.adjustments` 为 `["none","qfq","hfq"]`

#### Scenario: 非 A 股不可复权

- **WHEN** 描述符对应 `index`/`etf`/`fund`/`future`/`option`
- **THEN** `capabilities.bars.adjustments` 为 `["none"]`

### Requirement: 交易日历能力如实声明

系统 SHALL 声明 `capabilities.tradingCalendar` 为 `true`，因为上游提供近一年
A 股交易日序列且连接器已实现 `trading-calendar` 端点。

#### Scenario: 日历能力

- **WHEN** 调用 probe
- **THEN** `capabilities.tradingCalendar` 为 `true`
