$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# Run from the repository root.
# No Modal imports, network calls, deployments or GPU operations.

$env:PYTHONPATH = Join-Path $PWD "backend"

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & python @Arguments

    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "Python command failed with exit code $exitCode : python $($Arguments -join ' ')"
    }
}

Write-Host "Python version:"
Invoke-Python -Arguments @("--version")

Write-Host "Running local unit tests..."
Invoke-Python -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")

Write-Host "Compiling Python source..."
Invoke-Python -Arguments @(
    "-m",
    "py_compile",
    "backend/sonora/library.py",
    "backend/sonora/recovery.py",
    "backend/sonora/reconciliation.py",
    "tests/test_finalization_races.py",
    "tests/test_commit_acknowledgement.py",
    "tests/test_cleanup_receipt_recovery.py",
    "tests/test_lifecycle_reconciliation.py",
    "tests/test_cancellation_outcomes.py",
    "tests/test_publication_evidence.py",
    "tests/test_expensive_stage_guards.py",
    "tests/test_submission_recovery.py",
    "tests/test_cancellation_recovery.py",
    "backend/sonora/guard.py",
    "backend/sonora/worker.py",
    "tests/test_guard.py",
    "tests/test_worker.py",
    "tests/test_modal_observation.py",
    "backend/sonora/web.py",
    "scripts/run_local_studio.py",
    "tests/test_api.py",
    "infra/modal/sonora_staging.py",
    "tests/test_library.py",
    "tests/test_staging_isolation.py"
)

Write-Host "All local Phase 1 checks passed."
