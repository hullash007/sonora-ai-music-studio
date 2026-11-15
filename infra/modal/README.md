# Modal deployment baseline

These files are the current Modal deployment source files reviewed for the SONORA project. They are intentionally preserved as a baseline; do not deploy modified copies until changes have been tested.

## Files

- `ace_step_portal.py`: current CPU-hosted portal and separate on-demand GPU generation worker. Modal app name: `ace-step-instinct-portal`.
- `ace_step_api.py`: earlier standalone ACE-Step API deployment. Modal app name: `ace-step-15-api`.

## Existing Modal resources referenced by the code

- Volumes: `ace-step-checkpoints`, `ace-step-audio`
- Dict: `ace-step-jobs`
- Modal Secret names: `ace-step-portal` (portal password) and `ace-step-api` (standalone API configuration)
- ACE-Step source revision pinned by the portal: `ca1e85fe9430179831e6bc6be790c332190a3866`

Secret *names* are configuration references, not secret values. Never export or commit secret values, tokens, passwords, model checkpoints, or generated audio.

## Current portal deployment

The deployed portal is available at:

https://hullasho7--ace-step-instinct-portal-music-portal.us-east.modal.run/

The GPU worker is configured with `min_containers=0`, `max_containers=1`, and `scaledown_window=30`. The CPU portal has its own separate function configuration. Confirm actual Modal settings in the dashboard before relying on cost assumptions.

## Deploying

From the repository root, with the Modal CLI authenticated:

```powershell
modal deploy infra/modal/ace_step_portal.py
```

This command targets the same Modal app name as the current live portal. It can change the live deployment. Do not run it merely to test local changes.

For a safe experiment, first create a separate Modal app name and separate resources, or use a dedicated staging deployment. Keep the current live app untouched until a test plan passes.
