<#
T-44-4 観測手順固定（実ファイル構造に一致させる）

目的:
  - decisions の tail 観測が logs/decisions_*.jsonl を必ず参照する
  - size_decision の grep 観測が logs 配下で確実にヒットする
  - ops_history の meta.size_decision も併せて確認する

前提:
  - PowerShell 7
  - ここで参照する “実体” は以下（repoの実構造）:
      - logs/decisions_YYYY-MM-DD.jsonl
      - logs/ops/ops_result.jsonl

使い方（例）:
  pwsh -File .\tools\reobserve_decisions_tail.ps1
  pwsh -File .\tools\reobserve_decisions_tail.ps1 -Tail 200
#>

[CmdletBinding()]
param(
  [int]$Tail = 200
)

$ErrorActionPreference = 'Stop'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$logs = Join-Path $root 'logs'

Write-Host "root=$root"
Write-Host "logs=$logs"

# --- 1) 実体確認: logs/decisions_*.jsonl が存在すること ---
$decFiles = Get-ChildItem -Path $logs -Filter 'decisions_*.jsonl' -File -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending

if (-not $decFiles -or $decFiles.Count -le 0) {
  Write-Host "ERROR: logs/decisions_*.jsonl が見つかりません。" -ForegroundColor Red
  exit 2
}

$latestDec = $decFiles[0].FullName
Write-Host "latest_decisions=$latestDec"

# --- 2) tail: ENTRY 行の decision_detail.size_decision を観測 ---
Write-Host ""
Write-Host "=== decisions tail (last $Tail lines) ==="
$tailLines = Get-Content -Path $latestDec -Tail $Tail -ErrorAction Stop

Write-Host ""
Write-Host "=== decisions: ENTRY with decision_detail.size_decision ==="
$foundEntry = $false
foreach ($ln in $tailLines) {
  if (-not $ln) { continue }
  try { $o = $ln | ConvertFrom-Json -ErrorAction Stop } catch { continue }
  $dd = $o.decision_detail
  if ($null -eq $dd) { continue }
  $act = $dd.action
  if ($null -eq $act -or -not ($act.ToString().ToUpper().StartsWith('ENTRY'))) { continue }
  $sd = $dd.size_decision
  if ($null -eq $sd) { continue }
  $foundEntry = $true
  # 最小表示: ts + size_decision
  Write-Host ("ts={0} action={1} size_decision={2}" -f ($o.ts_jst ?? $o.timestamp), $act, ($sd | ConvertTo-Json -Compress))
}
if (-not $foundEntry) {
  Write-Host "WARN: tail 範囲に size_decision 付き ENTRY が見つかりません（Tail を増やしてください）。" -ForegroundColor Yellow
}

# --- 3) grep: logs 配下で size_decision がヒットすること ---
Write-Host ""
Write-Host "=== grep: logs/** contains 'size_decision' ==="
Get-ChildItem -Path $logs -Recurse -File -Include '*.jsonl','*.json' -ErrorAction SilentlyContinue |
  Select-String -SimpleMatch 'size_decision' -List |
  ForEach-Object { Write-Host ("hit: {0}" -f $_.Path) }

# --- 4) ops_history: logs/ops/ops_result.jsonl の meta.size_decision を観測 ---
$opsPath = Join-Path $logs 'ops\ops_result.jsonl'
if (Test-Path -Path $opsPath) {
  Write-Host ""
  Write-Host "ops_history=$opsPath"
  Write-Host "=== ops_history: meta.size_decision (tail $Tail) ==="
  $opsTail = Get-Content -Path $opsPath -Tail $Tail -ErrorAction Stop
  $foundOps = $false
  foreach ($ln in $opsTail) {
    if (-not $ln) { continue }
    try { $o = $ln | ConvertFrom-Json -ErrorAction Stop } catch { continue }
    $meta = $o.meta
    if ($null -eq $meta) { continue }
    $sd = $meta.size_decision
    if ($null -eq $sd) { continue }
    $foundOps = $true
    Write-Host ("started_at={0} symbol={1} meta.size_decision={2}" -f $o.started_at, $o.symbol, ($sd | ConvertTo-Json -Compress))
  }
  if (-not $foundOps) {
    Write-Host "WARN: tail 範囲に meta.size_decision が見つかりません（Tail を増やす / 実運用でENTRYを流してください）。" -ForegroundColor Yellow
  }
} else {
  Write-Host "WARN: ops_history not found: $opsPath" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "DONE"

