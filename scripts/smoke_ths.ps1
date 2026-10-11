# 真实上游冒烟：需先配置 THS_API_KEY 或 ~/.cloudtrade/config.json 的 ths.api_key
# 用法（必须 PowerShell 7+）：pwsh -File scripts\smoke_ths.ps1 [-Base http://127.0.0.1:8092]
#
# 说明：脚本依赖 -SkipHttpErrorCheck（PowerShell 7 引入），在 Windows PowerShell 5.1
# 下会报 NamedParameterNotFound。
param([string]$Base = "http://127.0.0.1:8092")

if ($PSVersionTable.PSVersion.Major -lt 7) {
    Write-Error "需要 PowerShell 7+（当前 $($PSVersionTable.PSVersion)）。请用 pwsh 运行。"
    exit 2
}

$ErrorActionPreference = "Continue"
$fail = 0

# 打印一行结果；期望状态码由调用方判定
function Show($name, $resp) {
    $snippet = $resp.Content
    if ($snippet.Length -gt 200) { $snippet = $snippet.Substring(0, 200) + "..." }
    Write-Output ("{0,-22} HTTP {1}  {2}" -f $name, $resp.StatusCode, $snippet)
}

# 构造日线请求体
function Bars($sym, $ex, $at, $extra = "") {
    '{"sourceId":"ths","instrument":{"id":"ths:' + $sym + '","symbol":"' + $sym +
    '","exchange":"' + $ex + '","providerRef":{"thscode":"' + $sym + '","assetType":"' + $at +
    '"}},"period":"daily","adjustment":"none","barAggregation":"original","limit":3' + $extra + '}'
}

function Post($path, $body) {
    Invoke-WebRequest "$Base$path" -Method POST -ContentType 'application/json' -Body $body `
        -TimeoutSec 40 -SkipHttpErrorCheck
}

Write-Output "=== probe（期望 200 且 periods 仅 daily）==="
$probe = Invoke-WebRequest "$Base/api/v1/market-data/sources/ths/probe" -TimeoutSec 40 -SkipHttpErrorCheck
Write-Output $probe.Content
if ($probe.StatusCode -ne 200) { $fail++ }
elseif ($probe.Content -notmatch '"periods":\["daily"\]') { Write-Output "  !! periods 不是仅 daily"; $fail++ }

Write-Output ""
Write-Output "=== 正向：五域日线（期望 200 + data）==="
$cases = @(
    @("A股 600519.SH",    (Bars "600519.SH" "SH" "a-share")),
    @("指数 000001.SH",   (Bars "000001.SH" "SH" "a-share-index")),
    @("ETF 510300.SH",    (Bars "510300.SH" "SH" "fund-etf")),
    @("期货 IC2612.CFE",  (Bars "IC2612.CFE" "CFFEX" "futures")),
    @("期权 10011425.SH", (Bars "10011425.SH" "SSE" "options"))
)
foreach ($c in $cases) {
    $resp = Post "/api/v1/market-data/bars" $c[1]
    Show $c[0] $resp
    if ($resp.StatusCode -ne 200) { $fail++ }
}

Write-Output ""
Write-Output "=== 负向：期望非 2xx + error.code ==="
# 1min 用例单独构造（周期字段与 Bars 不同）
$oneMin = '{"sourceId":"ths","instrument":{"id":"ths:600519.SH","symbol":"600519.SH",' +
    '"exchange":"SH","providerRef":{"thscode":"600519.SH","assetType":"a-share"}},' +
    '"period":"1min","adjustment":"none","barAggregation":"original","limit":3}'
$neg = @(
    @("1min → 400",     $oneMin,                                       400),
    @("指数+qfq → 400", (Bars "000001.SH" "SH" "a-share-index" ',"adjustment":"qfq"'), 400),
    @("未知标的 → 404",  (Bars "999999.SH" "SH" "a-share"),             404)
)
foreach ($n in $neg) {
    $resp = Post "/api/v1/market-data/bars" $n[1]
    Show $n[0] $resp
    if ($resp.StatusCode -ne $n[2]) { Write-Output "  !! 期望 HTTP $($n[2])"; $fail++ }
    elseif ($resp.Content -notmatch '"error"') { Write-Output "  !! 缺少 error 信封"; $fail++ }
}

Write-Output ""
if ($fail -eq 0) { Write-Output "全部通过" } else { Write-Output "$fail 项不符合预期"; exit 1 }
