# tools/export_tree_clean.ps1
param(
  [string]$Root = ".",
  [string]$OutFile = "project_tree.txt",
  [switch]$IncludeDirs = $true
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 完全に除外するディレクトリ名（必要なら追加）
$ExcludeNames = @(".git", ".venv", ".vscode", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages", "node_modules", "dist", "build")

function PathHasExcludedSegment {
  param([string]$FullPath)
  $norm = ($FullPath -replace '\\','/').TrimEnd('/')
  # パスをセグメントに分割して **完全一致** で判定
  $segs = $norm -split '/'
  foreach ($seg in $segs) {
    foreach ($ex in $ExcludeNames) {
      if ($seg -eq $ex) { return $true }
    }
  }
  return $false
}

Write-Host "🌳 Exporting clean tree from: $Root"
Write-Host "🧹 Excluding: $($ExcludeNames -join ', ')"

$lines = New-Object System.Collections.Generic.List[string]

# まずディレクトリ→次にファイル、の順で列挙（順序が安定）
Get-ChildItem -LiteralPath $Root -Recurse -Force -Directory -ErrorAction SilentlyContinue |
  Where-Object { -not (PathHasExcludedSegment $_.FullName) } |
  ForEach-Object {
    $rel = Resolve-Path -LiteralPath $_.FullName -Relative
    $rel = ($rel -replace '^[.][\\/]', '') -replace '\\','/'
    if ($IncludeDirs -and $rel) { $lines.Add($rel) }
  }

Get-ChildItem -LiteralPath $Root -Recurse -Force -File -ErrorAction SilentlyContinue |
  Where-Object { -not (PathHasExcludedSegment $_.FullName) } |
  ForEach-Object {
    $rel = Resolve-Path -LiteralPath $_.FullName -Relative
    $rel = ($rel -replace '^[.][\\/]', '') -replace '\\','/'
    if ($rel) { $lines.Add($rel) }
  }

$lines = $lines | Sort-Object
[System.IO.File]::WriteAllLines($OutFile, $lines, $Utf8NoBom)
Write-Host "✅ Wrote $OutFile (UTF-8, no BOM). Count=$($lines.Count)"
