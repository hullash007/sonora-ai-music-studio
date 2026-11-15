$ErrorActionPreference = "Stop"

# Run this script from the repository root. It uses only the local Python
# interpreter and standard-library test runner. It does not contact Modal.

$env:PYTHONPATH = "$PWD\backend"

Write-Host "Python version:"
python --version

Write-Host "Running local unit tests..."
python -m unittest discover -s tests -v

Write-Host "Compiling local Python source..."
python -m py_compile backend\sonora\library.py infra\modal\sonora_staging.py

Write-Host "Local Phase 1 checks passed. No deployment or remote calls were made."
