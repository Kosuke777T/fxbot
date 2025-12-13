param(
    [switch]$NoPause
)

Write-Host "=== Update API Docs ===" -ForegroundColor Cyan

# 1. venv確認（任意だが事故防止）

# 2. api_catalog 更新
Write-Host "[1/2] Generating api_catalog..." -ForegroundColor Yellow
python -X utf8 tools/gen_api_catalog.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "gen_api_catalog.py failed"
    exit 1
}

# 3. public_api 更新
Write-Host "[2/2] Generating public_api..." -ForegroundColor Yellow
python -X utf8 tools/gen_public_api.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "gen_public_api.py failed"
    exit 1
}

Write-Host "API docs updated successfully." -ForegroundColor Green

if (-not $NoPause) {
    Write-Host ""
    Read-Host "Press Enter to continue"
}