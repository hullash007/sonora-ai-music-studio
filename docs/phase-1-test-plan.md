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

- Python 3.10 and Python 3.12.15 unit/API/AST tests pass (47 tests each) without importing Modal.
- Headless Chrome: password rejection, fake generation completion, WAV playback (playhead advances), exact WAV download and library refresh persistence pass.
- Desktop/mobile screenshots inspected, no horizontal clipping. No external browser requests or JavaScript errors observed.
- PowerShell 7.7.0 on Linux: the runner passes with Python 3.12; a fake Python executable returning exit code 7 makes it throw and exit nonzero before the success message. Windows PowerShell itself is not tested. Paths use Join-Path and portable forward slashes.
- Real MP3 generation, Modal Volume commit/reload and deployment remain untested. No GPU or paid service was used.

## Guarded staging adapter verification (2026-11-15)

The expanded local suite has 125 passing tests on Python 3.10 and Python 3.12.15.
The PowerShell 7.7 runner passes on Linux and includes guard/worker compilation.
Windows has not been tested. These are local tests, not evidence of a successful
Modal deployment or real model generation.

The worker adapter permits one fixed 30-second validation generation, retries=0,
single-use containers, max one worker, hard 8-core/32-GiB resource limits, and
separate 600-second startup and 1200-second execution timeouts. The portal defaults
to generation disabled. Enablement and its password live only in the separate
staging Secret. The guard Dict must already exist and contain a separately
provisioned, fixed one-attempt run sentinel. The deployment code never creates or
refreshes that sentinel. No production resource names are reused.

Cancellation acceptance is not termination. The HTTP API returns 202 until the
function call is terminal, then records cancellation and claims cleanup once.
Unknown poll errors fail closed and require operator review. A malformed job
without its guard identity returns 503, even if ordinary state claims complete.
The library preserves completed tracks during cancellation cleanup.

Before any paid execution: publish and fetch-compare the complete tested tree,
verify staging Secret isolation and current included credits, test the stop path,
and establish bounded cost monitoring. Never retry or rearm the approved one-run
validation. Timeouts are not an exact dollar budget; build time, cold starts and
regional pricing can add cost. Do not claim the code enforces a credit-only or
exact $2 spending cap. Workspace-level limits are shared with production and
must not be changed for this validation.

### Stop-observation hardening

The adapter treats normal return, explicit function timeout/cancellation, pending
poll timeout, and unknown errors separately. Unknown RPC/runtime/deserialization
errors never prove stop. Call graphs are not used as a cleanup gate. Cancellation
cleanup additionally requires live function statistics showing no backlog, no
running inputs, and zero worker containers. Timeout without cancellation records
failed where observed, but preserves partial data for manual review. Interrupted
finalization and unknown call observations report reconciliation_required without
claiming successful finalization or deleting files. Cleanup failure is persistent
status information and is never silently retried. Eight AST-extracted adapter
tests execute the exact nested observation functions without importing Modal or
contacting its service.
