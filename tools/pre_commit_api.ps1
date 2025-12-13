param(
    [string]$Message = "chore: update API catalogs"
)

Write-Host "=== Pre-Commit: API Docs ===" -ForegroundColor Cyan

# git チェック
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Not a git repository"
    exit 1
}

# 1. API docs 更新
pwsh tools/update_api_docs.ps1 -NoPause
if ($LASTEXITCODE -ne 0) {
    Write-Error "API docs update failed"
    exit 1
}

# 2. 差分確認
$changed = git status --porcelain docs/api_catalog.md docs/api_catalog.json docs/public_api.md
if (-not $changed) {
    Write-Host "No API doc changes detected." -ForegroundColor Green
    exit 0
}

Write-Host "Changed files:" -ForegroundColor Yellow
$changed

# 3. git add
git add docs/api_catalog.md docs/api_catalog.json docs/public_api.md

# 4. commit
git commit -m $Message
if ($LASTEXITCODE -ne 0) {
    Write-Error "git commit failed"
    exit 1
}

Write-Host "Committed API doc updates." -ForegroundColor Green
