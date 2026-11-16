# Set up Sonora locally

This guide runs the application on your own computer with **fake audio generation**. It needs no Google account, Modal account, model weights, GPU or paid service. The Generate button creates a two-second WAV test tone, not AI music, regardless of the requested track length. The interface may still use production labels such as "MP3" and "ACE-Step"; those labels do not change this local mode.

Real AI generation is a separate, incomplete packaging path. See [the deployment gap](#real-ai-generation-deployment-gap) before planning a hosted copy.

## Requirements

- Git, or a downloaded and extracted repository ZIP.
- Python 3.12 and its `venv` module.
- Node.js 22.12 or later in the Node 22 line, or another Node version supported by the pinned Vite package. The checks below used Node 22.23.4 and npm 10.9.9.
- A terminal and a modern browser. Run commands from the repository root unless a step says otherwise.

The current source is on `release/google-studio`, the default branch. The older `main` branch is not this release.

## 1. Get the current source

```sh
git clone --branch release/google-studio https://github.com/hullash007/sonora-ai-music-studio.git
cd sonora-ai-music-studio
```

Alternatively, use GitHub's Code > Download ZIP while viewing `release/google-studio`, extract it and open a terminal in the extracted folder.

## 2. Create a Python environment

macOS / Linux:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python`; you do not need to change machine-wide execution policy.

These dependencies run the API, tests and local fixture. The Modal SDK is included because a historical adapter test imports it; installation does not sign in, deploy or run a GPU. They do not install ACE-Step or its weights.

## 3. Build the browser interface

```sh
cd frontend
npm ci
npm test
npm run build
cd ..
```

`npm ci` uses the checked-in lockfile. The build writes files into `backend/sonora/static/`. Those files are served by the Python application. You do not need a separate frontend development server for this guide.

## 4. Run the API checks

macOS / Linux:

```sh
PYTHONPATH=backend python -m unittest discover -s tests
```

Windows PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\backend"
python -m unittest discover -s tests
```

The checked release passes 229 Python tests and 39 frontend tests. Tests use local fixtures; passing them does not verify a real GPU deployment.

## 5. Start the local studio

```sh
python scripts/run_local_studio.py --host 127.0.0.1 --port 8000 --data-dir .local-sonora-data
```

Open `http://127.0.0.1:8000` in your browser. The local-only demo password is `local-sonora-demo-password`. This is a published development value, not a credential for the hosted Sonora service. Never expose this fixture server to the internet.

To choose another local password, set `SONORA_LOCAL_PASSWORD` in your shell before starting the server. See [`.env.example`](../.env.example). The application reads process environment variables; it does **not** load `.env` files automatically. Do not commit your own password or paste it into an issue.

Keep the bind address at `127.0.0.1`. This fixture uses a shared development password and is not the hosted Google-sign-in service.

## 6. Check that it works

1. Enter the local demo password and open the workspace.
2. Confirm the header reads "SONORA by Hullash".
3. Describe any sound, keep 30 seconds selected and click Generate.
4. A two-second WAV tone should appear. Play it, download it, then open Library to find the saved track.
5. Stop the server with Ctrl+C. Restart with the same `--data-dir`; the saved library file should remain.

The local queue/job-status mapping exists only while the process runs. After a restart, a remembered active-job indicator can fail its status check; the saved Library is the place to find completed fixture audio. Drafts, projects and preferences are browser-local. The chosen data directory stores fixture audio/library records, not model weights. Delete it only if you intend to remove that local library.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `python3.12` or `py -3.12` not found | Install Python 3.12, then open a new terminal. |
| `No module named fastapi` or `uvicorn` | Activate the environment, or use its Python executable, and install `requirements-dev.txt`. |
| npm engine/version error | Check `node --version`; use a Node version supported by the pinned Vite package. |
| Missing/old UI or asset 404 | Run `npm ci` and `npm run build` inside `frontend`, then reload the page. Source and built assets must come from the same release. |
| Port already in use | Start with `--port 8001`, then open `http://127.0.0.1:8001`. |
| Google sign-in is absent | Expected: the local fixture uses a development password, not Google OAuth. |
| Only a beep, not music | Expected: this guide intentionally runs fake generation without a model or GPU. |
| Library disappeared | Use the same `--data-dir` and check whether the directory was removed. Browser drafts are separate from library audio. |
| Local `/api/shared-studio` returns 404 | Expected for this fixture. The interface falls back to the 30-second input choice. |

## Real AI generation: deployment gap

**A fresh real-generation deployment is not yet reproducible from this repository alone.** No clean-account GPU deployment was performed for this guide. Do not describe the local fixture as proof that AI inference is installed.

The hosted release adapter (`infra/modal/sonora_shared.py`) depends on:

- Your own Modal workspace, credentials, GPU allowance and billing controls.
- A prepared Python 3.12 GPU image containing the pinned ACE-Step source, its environment, FFmpeg and compatible CUDA/PyTorch dependencies.
- Model weights/checkpoints, provisioned in your own persistent storage under their applicable terms.
- A verification package at `/opt/sonora-validation-prep/`, including **`prepare.py`, `source-pins.json` and `model-manifest.json`**. These preparation/build assets are **not included** here. The worker imports and checks them before generation. Do not bypass these checks to make deployment appear to work.
- Your own Google OAuth web client and correct HTTPS origin, server-side secret and session storage.
- Your own audio volume, jobs/state records, privacy contact, budget, cutoff and tested cancellation/settlement behavior.

[The deployment configuration example](../infra/modal/deployment.example.json) is an annotated planning inventory, not a loader or an executable deployment. It deliberately contains placeholders and no working credentials. The existing adapters also contain release-specific image IDs, resource names, origin, budget and dates. Replacing only the environment variables is insufficient. Do not point a new installation at the original operator's resource names.

`infra/modal/ace_step_api.py`, `sonora_owner.py` and `sonora_staging.py` describe other historical deployment stages. They are not drop-in replacements for the current Google-authenticated studio. Do not run them as a shortcut for this release.

A future self-contained GPU package still needs the missing image-preparation and model-verification assets, a configurable adapter without original-deployment defaults, a documented first-deployment resource/bootstrap sequence, and a separately authorized clean-account inference test. Those are remaining work, not completed setup steps.

## Costs, privacy and license

- This local guide does not use the hosted operator's credits or private library and does not make an outbound model request.
- Hosting your own real-generation copy uses **your** hosting account and compute allowance. Opening the original hosted service and generating there instead uses that service operator's resources.
- Keep private credentials out of Git, configuration examples, screenshots and issue reports. Use the provider's secret store for deployed secrets.
- The application source is MIT-licensed. ACE-Step, model weights and other dependencies have their own terms; the app's license does not replace them.

Verified on 16 November 2026: clean Python 3.12 dependency installation, local test suites, frontend build and local browser fixture. Real Google login on a new domain and new-account GPU inference remain unverified.
