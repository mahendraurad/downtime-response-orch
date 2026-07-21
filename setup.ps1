$ErrorActionPreference = "Stop"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python -or $python.Source -like "*WindowsApps*") {
    throw "Python is not installed (the WindowsApps alias is not a Python runtime). Install Python 3.10+ with 'Add python.exe to PATH' enabled, then rerun .\setup.ps1."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "Setup complete."
Write-Host "Activate:  .\.venv\Scripts\Activate.ps1"
Write-Host "Test:      python -m pytest -q"
Write-Host "Agents:    python scripts\run_agent.py --help"
Write-Host "API:       python -m uvicorn src.api.main:app --reload"
Write-Host "Docs:      http://127.0.0.1:8000/docs"
