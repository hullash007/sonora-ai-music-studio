# Historical production baseline snapshot (reviewed 2026-11-15)

This describes the earlier production portal, not the current SONORA public preview. SONORA UI01-07 is a separate isolated, read-only, generation-disabled staging deployment. See RELEASE-UI07-LIVE.md for its verified identity and limits. Baseline generation claims below were inherited from the earlier technical conversation and are not a new end-to-end GPU verification. Do not read this historical snapshot as permission or readiness to enable SONORA generation.

## Verified state

- The current music portal is deployed on Modal and can generate music.
- The portal is a FastAPI application served by a CPU-only Modal function.
- Music generation is delegated to a separate Modal GPU function.
- Generation jobs are coordinated through Modal Dict `ace-step-jobs`.
- Audio and checkpoints use Modal Volumes `ace-step-audio` and `ace-step-checkpoints`.
- The portal source pins an ACE-Step Git commit rather than tracking a moving upstream branch.
- The portal uses a Modal Secret named `ace-step-portal` for its access password. The secret value is not present in source control.

## Findings for future work

1. The portal UI currently exposes a prompt, optional lyrics, and duration only.
2. The generation parameter uses `vocal_language="unknown"`; a language selector requires confirming the supported values and behavior in the pinned ACE-Step revision.
3. The code stores the requested duration in job metadata. It should later record the actual audio duration by inspecting the generated file.
4. The GPU worker initializes the DiT and language model in the function invocation. Measure cold-start and repeated-job behavior before changing container lifecycle or model caching.
5. The UI does not provide a persistent searchable track library, account system, favorites, renaming, or version management.
6. Job and audio retention/reconciliation need an explicit lifecycle policy.

## Safe development rule

Treat the two source files in `infra/modal/` as a baseline snapshot. Implement SONORA changes in a separate branch and use a staging Modal app/resources for tests. Do not replace the live deployment until the new version passes generation, download, persistence, and scale-to-zero checks.
