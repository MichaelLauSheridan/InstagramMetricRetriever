$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}

$env:STREAMLIT_BROWSER_GATHER_USAGE_STATS = "false"
& ".venv\Scripts\python.exe" -m streamlit run app.py
