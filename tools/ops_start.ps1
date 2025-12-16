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
  if ($Profiles.Count -le 1) {
    # 単一プロファイルの場合は --profile を使用
    $cmd += @("--profile", $Profiles[0])
  } else {
    # 複数プロファイルの場合は --profiles を使用
    $cmd += @("--profiles", ($Profiles -join ","))
  }
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

# --- $out から「最後のJSON行」を抽出（ログ行が混ざってもOKにする） ---
$jsonLine = $null
try {
  if ($out -is [System.Array]) {
    # 末尾から探す（最後に出るJSONが欲しい）
    $jsonLine = $out | Where-Object { $_ -and $_.TrimStart().StartsWith("{") } | Select-Object -Last 1
  } else {
    # 文字列の場合は改行で分割して末尾から探す
    $lines = [string]$out -split "`r?`n"
    $jsonLine = $lines | Where-Object { $_ -and $_.TrimStart().StartsWith("{") } | Select-Object -Last 1
  }
} catch {
  $jsonLine = $null
}

# --- ops_result.jsonl へ履歴追記（CLI実行でも履歴を残す） ---
try {
  $histDir = Join-Path $PSScriptRoot "..\logs\ops"
  New-Item -ItemType Directory -Force -Path $histDir | Out-Null
  $histDir = (Resolve-Path $histDir).Path
  $histPath = Join-Path $histDir "ops_result.jsonl"

  if ($jsonLine -and $jsonLine.TrimStart().StartsWith("{")) {
    $obj = $jsonLine | ConvertFrom-Json

    # profiles は multi の場合は per_profile のキー、single は profile を配列化
    $profiles = @()
    if ($obj.per_profile) {
      $profiles = @($obj.per_profile.PSObject.Properties.Name)
    } elseif ($obj.profile) {
      $profiles = @([string]$obj.profile)
    }

    # model_path は top-level outputs か、per_profile の先頭から拾う
    $modelPath = $null
    if ($obj.outputs -and $obj.outputs.model_path) {
      $modelPath = [string]$obj.outputs.model_path
    } elseif ($obj.per_profile) {
      foreach ($p in $profiles) {
        $pp = $obj.per_profile.$p
        if ($pp.outputs -and $pp.outputs.model_path) { $modelPath = [string]$pp.outputs.model_path; break }
      }
    }

    # started_at のISO化（必須）
    $startedIso = $null
    if ($obj.started_at) {
      try { $startedIso = (Get-Date $obj.started_at).ToString("o") } catch { $startedIso = $null }
    }
    if (-not $startedIso) { $startedIso = (Get-Date).ToString("o") }

    # apply抽出
    $applyPerformed = $false
    $applyReason = $null
    if ($obj.apply) {
      if ($obj.apply.performed -ne $null) { $applyPerformed = [bool]$obj.apply.performed }
      if ($obj.apply.reason) { $applyReason = [string]$obj.apply.reason }
    }

    # ended/elapsed/type
    $endedIso = $null
    if ($obj.ended_at) { try { $endedIso = (Get-Date $obj.ended_at).ToString("o") } catch { $endedIso = $null } }
    $elapsedSec = $null
    if ($obj.elapsed_sec -ne $null) { $elapsedSec = [double]$obj.elapsed_sec }
    $typ = $null
    if ($obj.type) { $typ = [string]$obj.type }

    $rec = [ordered]@{
      ts             = (Get-Date).ToString("o")
      type           = $typ
      symbol         = [string]$Symbol
      profiles       = $profiles
      started_at     = $startedIso
      ended_at       = $endedIso
      elapsed_sec    = $elapsedSec
      ok             = [bool]$obj.ok
      step           = [string]$obj.step
      model_path     = $modelPath
      apply_performed = $applyPerformed
      apply_reason    = $applyReason
    }

    ($rec | ConvertTo-Json -Compress) | Add-Content -Path $histPath -Encoding UTF8
  }
} catch {
  # 履歴追記が失敗しても ops_start 自体を落とさない
}

exit $rc

