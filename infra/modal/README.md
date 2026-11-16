# SONORA Modal staging source

`sonora_staging.py` defines a separate staging application. It must remain isolated from the currently working production Modal application.

## Staging-only resources declared in source

| Resource | Staging name |
|---|---|
| Modal app | `sonora-staging` |
| Checkpoint Volume | `sonora-staging-checkpoints` |
| Audio Volume | `sonora-staging-audio` |
| Job Dict | `sonora-staging-jobs` |
| Password Secret | `sonora-staging-portal` |

The staging code does not mount or read production Volumes, job records, or Secrets. Its library is initially empty; it will not display historical production tracks.

The staging audio Volume layout is:

```text
/mnt/sonora-staging-audio/
├── jobs/<job-id>/<generated-file>.mp3
└── library/<job-id>.json
```

## Local testing

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\backend"
python -m unittest discover -s tests -v
python -m py_compile backend\sonora\library.py infra\modal\sonora_staging.py
```

These local commands do not run the Modal module or contact Modal. See `docs/phase-1-test-plan.md`.

## Deployment status

**Not deployed. Do not deploy without explicit user approval.** Deployment and GPU generation are intentionally excluded from Phase 1 local tests. Any future deployment requires a separately created `sonora-staging-portal` Secret containing `PORTAL_PASSWORD` and explicit approval for the associated Modal/GPU/storage operations.

No secret values, model weights, generated audio, or credentials belong in this repository.
