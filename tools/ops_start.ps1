[CmdletBinding()]
param(
  [string]$Symbol = "USDJPY-",
  [int]$Dry = 0,
  [int]$CloseNow = 1,
  [string[]]$Profiles = @()
)

# --- UTF-8 出力を強制（日本語ログ文字化け対策） ---
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
# -----------------------------------------------

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

# Profiles が未指定 or 空の場合、config/profiles.json から補完
if (-not $Profiles -or $Profiles.Count -eq 0) {
  $configPath = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "config\profiles.json"
  if (Test-Path $configPath) {
    try {
      $configData = Get-Content -Path $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($configData.profiles -and $configData.profiles.Count -gt 0) {
        $Profiles = @($configData.profiles | ForEach-Object { [string]$_ })
      }
    } catch {
      # 読み込み失敗時は無視（デフォルトにフォールバック）
    }
  }
}

# それでも空ならデフォルト
if (-not $Profiles -or $Profiles.Count -eq 0) {
  $Profiles = @("michibiki_std")
}

# stdout を1行として受け取る（Python側はASCII JSON想定）
$cmd = @(
  $py, "-X", "utf8", "-m", "scripts.walkforward_retrain",
  "--symbol", $Symbol
)

if ($Profiles -and $Profiles.Count -gt 0) {
  $cmd += @("--profiles", ($Profiles -join ","))
}

# 既存の --dry / --emit-json 等もここに続く
# --dry は walkforward_retrain.py では --dry-run として扱われる
if ($Dry -eq 1) {
  $cmd += @("--dry-run")
}

# JSON出力を有効化（既存のログ処理と互換性を保つ）
$cmd += @("--emit-json", "1")

$out = & $cmd[0] @($cmd[1..($cmd.Count-1)])
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

