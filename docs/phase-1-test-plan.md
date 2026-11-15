# Phase 1 — Local Test Plan

## Safety boundary

These checks are local-only. They do not import the Modal deployment module, authenticate with Modal, call the Modal API, deploy an app, start a GPU, download model weights, or consume Modal credits.

Do not run `modal deploy`, `modal run`, or any remote function invocation during this phase.

## Prerequisites

- Python 3.12 recommended (the staging Modal image is configured for Python 3.12).
- Git.
- PowerShell on Windows.
- Install local API/browser-runner dependencies: `python -m pip install -r requirements-dev.txt`.
- Library-only tests still use only the standard library. API tests use FastAPI and HTTPX.
- Run `python scripts/run_local_studio.py`, then open http://127.0.0.1:8000. Default demo password: `local-sonora-demo-password`. Set `SONORA_LOCAL_PASSWORD` to override. The loopback-only default must not be exposed to the internet.
- Local generation writes a two-second WAV tone, not real music. Library files persist under the system temporary directory in `sonora-local-studio`; job status is in-memory and resets on server restart.

## PowerShell commands

Run from the repository root:

```powershell
$env:PYTHONPATH = "$PWD\backend"
python --version
python -m unittest discover -s tests -v
python -m py_compile backend\sonora\library.py backend\sonora\web.py infra\modal\sonora_staging.py scripts\run_local_studio.py
```

Expected result: all unit tests pass and the listed Python files compile without output/errors. `py_compile` only compiles source; it does not execute the Modal deployment module.

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

## Local verification recorded November 15, 2026

- Python 3.10 unit/API/AST tests pass without importing Modal. Target Python 3.12 is not runtime-tested yet.
- Headless Chrome: password rejection, fake generation completion, WAV playback (playhead advances), exact WAV download and library refresh persistence pass.
- Desktop/mobile screenshots inspected, no horizontal clipping. No external browser requests or JavaScript errors observed.
- PowerShell fail-fast behavior is source-reviewed, not executed on Windows.
- Real MP3 generation, Modal Volume commit/reload and deployment remain untested. No GPU or paid service was used.
