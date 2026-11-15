# Phase 1 — Persistent Music Library

## Goal

Add a browsable music library to the existing ACE-Step generation workflow. Each successful staging generation is recorded as a JSON metadata file alongside its MP3 in a persistent staging-only Volume. The browser can list, play, and download completed tracks.

## Scope in this package

- A separate Modal app named `sonora-staging`.
- Separate staging-only resources:
  - Volume: `sonora-staging-checkpoints`
  - Volume: `sonora-staging-audio`
  - Dict: `sonora-staging-jobs`
  - Secret: `sonora-staging-portal`
- CPU-hosted portal and separate on-demand GPU worker.
- JSON metadata per completed track under `library/<uuid>.json` on the staging audio Volume.
- Library API endpoints for listing completed tracks and retrieving audio.
- Browser controls to refresh the library, play a track, and download its MP3.
- Standard-library helper functions for validation, atomic metadata writes, listing, and safe audio-path resolution.
- Local unit tests that do not import Modal or make network calls.

## Isolation guarantee

This package does not reference the current production app, production Volumes, production Dict, or production Secret. It starts with an empty staging library. It does not automatically import tracks created in production. Existing production resources and their data are not mounted by this app.

A dedicated checkpoints Volume is included to keep staging fully separate. When staging is eventually deployed and a generation is explicitly approved, model checkpoints may need to be downloaded into this separate Volume. That is a deployment/runtime operation and is not performed by local tests.

## Storage layout

```text
/mnt/sonora-staging-audio/
├── jobs/
│   └── <job-id>/
│       └── <generated-audio>.mp3
└── library/
    └── <job-id>.json
```

Each metadata record stores job ID, title, status, original prompt, lyrics, filename, relative audio path, requested duration, timestamps, engine, and pinned model revision. Metadata is written atomically using a temporary file followed by `os.replace`; the worker commits the Volume before reporting the job complete.

The Modal Dict is used only for live job status. The persistent library index is stored on the staging audio Volume, so the library does not rely on job-status record retention.

## API surface

| Endpoint | Access | Purpose |
|---|---|---|
| `GET /` | Public page | Render the staging studio |
| `GET /api/health` | Public | CPU portal health check |
| `POST /api/generate` | Staging password | Validate and queue a generation job |
| `GET /api/status/{job_id}` | Staging password | Read live generation status |
| `GET /api/library` | Staging password | List completed staging tracks |
| `GET /api/library/{job_id}/audio` | Staging password | Stream a track for playback/download |

The health and page routes do not access the GPU. Only submitting a generation job invokes the GPU worker.

## Out of scope

- Production migration or production storage access.
- Importing existing production tracks.
- Multi-user authentication, tenancy, playlists, renaming, favorites, or sharing.
- PostgreSQL and object storage migration.
- Model initialization optimization.
- Language/genre/tempo controls beyond the current prompt and lyrics inputs.
- Deployment, GPU execution, checkpoint download, and paid Modal operations.

## Acceptance criteria

1. Local unit tests pass without Modal installed or authenticated.
2. Static checks confirm staging resource names are distinct.
3. A completed generation produces one MP3 and one durable JSON record in the staging Volume.
4. The library endpoint lists completed staging records newest-first.
5. A browser can play and download a listed track through an authenticated endpoint.
6. Invalid IDs and audio path traversal attempts are rejected.
7. Production configuration and storage remain unchanged.

## Remaining deployment blockers

- User approval is required before any Modal deployment, GPU invocation, paid service, or credit-consuming test.
- The staging Secret `sonora-staging-portal` must be created separately with `PORTAL_PASSWORD`; no password value belongs in Git.
- The exact Modal CLI version and the availability of the pinned upstream ACE-Step commit must be confirmed at deployment time.
- The separate staging checkpoint Volume will be empty initially; model download and initialization will require an approved GPU test.
- The deployment needs a short-duration generation acceptance test to confirm ACE-Step dependency compatibility, GPU capacity, model startup, audio commit/reload, and end-to-end library behavior.
- Local tests cannot prove Modal Volume semantics, GPU behavior, or the actual deployment's CPU/GPU billing characteristics.
- Review the public repository before production use; it is source-public and must never contain secrets, generated audio, or model weights.
