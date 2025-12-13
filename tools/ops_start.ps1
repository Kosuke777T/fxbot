[CmdletBinding()]
param(
  [string]$Symbol = "USDJPY-",
  [int]$Dry = 0,
  [int]$CloseNow = 1
)

$ErrorActionPreference = "Stop"

# venv python を優先（なければ python）
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (!(Test-Path $py)) {
  $py = "python"
}

# logs\ops にJSONLで退避（1行1実行）
$logDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "logs\ops"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("ops_start_{0}.jsonl" -f (Get-Date -Format "yyyyMMdd"))

# stdout を1行として受け取る（Python側はASCII JSON想定）
$out = & $py -X utf8 -m tools.ops_start --symbol $Symbol --dry $Dry --close-now $CloseNow
$rc = $LASTEXITCODE

# 画面出力（機械判定用にそのまま）
if ($out) { $out | ForEach-Object { $_ } }

# ログへ追記（空でも行を残す：後で調査が楽）
# 1行に正規化（JSONLを壊さない）
$line = ""
if ($out) {
  if ($out -is [System.Array]) {
    $line = ($out -join "")
  } else {
    $line = [string]$out
  }
}
$line = $line.TrimEnd("`r","`n")
Add-Content -Path $logFile -Value $line -Encoding UTF8

exit $rc

