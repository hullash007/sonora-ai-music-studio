# SONORA - AI Music Studio

A web app that turns a music description into an AI-generated track. Built with React, FastAPI, ACE-Step 1.5 and on-demand Modal GPUs.

This is the `release/google-studio` source snapshot. It contains the current Google-sign-in studio; `main` and older architecture notes describe earlier stages of the project.

## What works

- A simple create flow: describe the music, choose a length, generate, then listen or download an MP3.
- Fixed 30, 60, 90 and 120-second generation choices in the deployed profile.
- Instrumental mode or optional lyrics. Extra genre, mood, instrument and arrangement directions are collapsed by default.
- Google sign-in with server-verified identity, server-backed sessions and account-owned audio access.
- A private searchable track library with browser playback and downloads.
- Browser-local drafts, projects, preferences and validated JSON backup/import.
- Asynchronous job status, one active GPU job at a time, conservative credit admission, cancellation/reconciliation handling and no automatic model retries.
- A responsive blue-on-warm-gray interface with desktop navigation and a phone bottom bar.

Creative directions guide the model. They are not guaranteed audio parameters or a timeline/audio editor. Drafts and projects do not sync across devices. The app does not include stem separation, mastering, collaborative editing or track variations.

## Architecture

```text
React / Vite browser client
        |
FastAPI web service on Modal CPU
  |     |                 |
Google identity      Account-owned audio
  |                  in persistent storage
Guarded asynchronous job
        |
Single on-demand A10 or L4 worker
        |
ACE-Step 1.5 Turbo + 1.7B language model
        |
MP3 validation and durable publication
```

The configured GPU worker has 8 CPU cores and 32 GiB RAM. Job and admission records use durable key/value state; model checkpoints and audio use Modal Volumes. No GPU runner is kept warm.

## Verification boundaries

- 229 Python tests and 39 frontend tests pass for this release.
- Live end-to-end generation has been checked at 30 and 120 seconds, including saved audio and authenticated playback.
- 60 and 90 seconds have offline parameter, admission, media and UI checks, not separate live model runs.
- The measured 120-second output was 120.024 seconds. A single run is not a generation-speed or music-quality guarantee.
- Redesign checks cover empty, busy, completed and error states with local fixtures, responsive widths, drafts and project persistence. They do not spend inference credits.
- Account-isolation tests pass. Live checks used the owner account, not a second person's real Google account.

## Try it on your computer

Start with the [step-by-step local setup guide](docs/setup.md). It covers macOS/Linux and Windows, dependencies, building the interface, running the local fixture and troubleshooting. No paid services are needed for that path. The local Generate button creates a two-second WAV test tone, not AI music.

Configuration references: [local environment example](.env.example) and [hosted deployment planning inventory](infra/modal/deployment.example.json). Neither contains real credentials.

## Run the checks

Use Python 3.12 for the tested backend environment and a current Node.js environment compatible with the pinned Vite version.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=backend python -m unittest discover -s tests

cd frontend
npm ci
npm test
npm run build
```

To explore the local fake studio, run `python scripts/run_local_studio.py --help` from the repository root. This is a development fixture, not live model inference or the deployed Google identity flow.

## Deploying your own copy

**Real-generation self-hosting is not yet reproducible from this repository alone.** The prepared GPU-image builder and model-verification package (`prepare.py`, `source-pins.json`, `model-manifest.json`) are missing. See the [exact remaining setup gaps](docs/setup.md#real-ai-generation-deployment-gap). A fresh account deployment has not been verified.

Read [the current release notes](docs/public-release.md) first. The included adapters are sanitized source, not a one-command copy of an existing deployment. Configure your own Google OAuth client, session secret, privacy contact, Modal resources, model/checkpoint pins, budget limits and cutoff. Preserve or migrate existing track history before replacing a running deployment.

The source defaults to 30-second generation unless the accepted duration list is configured. Do not enable longer lengths or widen credit limits without verifying the runtime, output checks and cost controls for your deployment. A fixed application budget is not a real-time provider credit meter; billing can lag.

## Repository map

- `frontend/`: React interface and frontend tests
- `backend/`: API, library, identity and job services
- `music-engine/`: model integration
- `infra/`: deployment adapters
- `tests/`: backend and safety regression tests
- `scripts/`: local development and verification helpers
- `docs/`: current release notes plus historical design documents

## License and upstream model

Application source is provided under the repository's [MIT license](LICENSE). Model weights, dependencies and generated content have their own applicable terms. Review those separately before redistribution or commercial use.

Music generation uses [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5). SONORA supplies the application interface, account boundaries, job workflow and deployment integration around that engine.
