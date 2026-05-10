# Run from repo root OR double-click: imports resolve because cwd = backend
Set-Location $PSScriptRoot
Write-Host "Starting API from: $(Get-Location)"
uvicorn main:app --reload --port 8000
