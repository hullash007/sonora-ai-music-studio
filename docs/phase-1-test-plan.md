# Phase 1 — Local Test Plan

## Safety boundary

These checks are local-only. They do not import the Modal deployment module, authenticate with Modal, call the Modal API, deploy an app, start a GPU, download model weights, or consume Modal credits.

Do not run `modal deploy`, `modal run`, or any remote function invocation during this phase.

## Prerequisites

- Python 3.12 recommended (the staging Modal image is configured for Python 3.12).
- Git.
- PowerShell on Windows.
- No third-party Python package is required for the local library unit tests; tests use `unittest` from the standard library.

## PowerShell commands

Run from the repository root:

```powershell
$env:PYTHONPATH = "$PWD\backend"
python --version
python -m unittest discover -s tests -v
python -m py_compile backend\sonora\library.py infra\modal\sonora_staging.py
```

Expected result: all unit tests pass and both Python files compile without output/errors. `py_compile` only compiles source; it does not execute the Modal deployment module.

## What the tests cover

- Writing and reading a completed-track metadata record.
- Listing tracks newest-first.
- Rejecting malformed job IDs.
- Rejecting path traversal and absolute paths.
- Rejecting non-completed records.
- Ignoring corrupt metadata JSON.
- Ensuring record IDs match their metadata filenames.
- Validating requested list limits.
- Ensuring the staging app uses its own app, Volumes, Dict, and Secret names.

## Git steps

After the package is merged into the repository:

```powershell
git status --short
git add backend\sonora infra\modal\sonora_staging.py infra\modal\README.md tests docs\phase-1-library.md docs\phase-1-test-plan.md
git commit -m "feat: implement isolated staging music library"
git push
```

Review `git status --short` before staging files. Do not add `.env`, password files, downloaded models, checkpoints, or generated audio.

## Not covered locally

Local tests do not prove Modal deployment behavior, Volume commit/reload semantics, ACE-Step initialization, GPU capacity, generated MP3 duration, browser playback against a deployed service, or actual cost. Those checks require a separately approved staging deployment and generation test.
