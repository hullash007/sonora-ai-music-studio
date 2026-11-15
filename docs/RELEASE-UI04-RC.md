# SONORA UI01-04 release candidate

Frozen feature source:273ab851bf3fdc3bb062929366a0bf9ce6f6a1f6.
Branch:feat/phase-1-library. Production/main unchanged:1c059b7e66f8a85cee2d5e1cbebb3ad0339cd3a7.
QA completed 2026-11-15 23:35 IST in clean isolated worktree.

## Increment history

- UI01:3236524
- UI01 polish:f07eb8f4d4937361c3146d95d2973e7f3bc8d4b3
- UI02:a2376db00587847900ad0ab262d9a5b7d81d6e2f
- UI03:f3a61c778d2d3a6cceeb5d21f864b75e8395d40b
- UI04:273ab851bf3fdc3bb062929366a0bf9ce6f6a1f6

## Verified locally

- Fresh lockfile install, Vite build and byte-identical rebuilt assets.
-18 Node unit tests; production dependency audit zero findings.
-135 Python tests and compile via PowerShell on each Python3.10 and3.12.
-30 JavaScript/FastAPI validation parity cases, Unicode/limits/types, zero enqueue callbacks.
-65 public-preview security requests:route/method allowlist, traversal, mutations denied, zero callbacks.
- UI02/03/04 interaction suites at1440/1024/768/390/320 pixels; fresh-profile and keyboard focus/dialog/Escape tests.
- No JS errors, horizontal overflow, external requests or mutation requests in clean UI browser run.
- Actual desktop/tablet/mobile pixels inspected.390x480 short viewport is a simulation, not physical keyboard evidence.
- Empty clean profiles, no seeded demo state, password-free preview. Generation OFF; no GPU/model download/production changes.

## Asset SHA-256

- index.html:efba05889b8c363d9206926c3705eec0f6c05ff580cb300dfdc056ef834f0d66
- app.js:8a48242c271b461b32dea5f153f0100de30895c24f1f8f7d8a7a129aafc2b429
- app.css:304e9c44020a06b5e447071723d259b0557d61daf8c28d21d6c978aeeda1642f

## Publication provenance and next gate

Browser-based GitHub file commits have different commit IDs from local commits. Preserve increment history and verify complete Git tree identity before deployment; record actual remote SHA/tree and Actions run in release closeout. No claim of identical commit metadata is made. Repeat tree verification after claim/manifest/marker changes. The push-triggered workflow checks out immutable GITHUB_SHA, not a moving branch. Log and verify checked-out HEAD equals GITHUB_SHA before any deployment command.

UI status is local verified until live health, byte hashes, responsive interactions and public security checks pass. A workflow success alone is not acceptance. Temporary token and repository secrets must be removed afterward. Preserve all deployment claim history. CPU-only budget:stop at displayed$4.50, absolute$5.00. No card/GPU/generation/model activity.

Publication normalization:GitHub rename adds a trailing newline. Vite now normalizes final JS newlines; rebuilt bytes equal renamed upload, with no feature/runtime changes.18 Node tests rerun PASS. HTML/CSS unchanged. New immutable claim:sonora:deployment-validation:2026-11-15:ui04-rc1-273ab851bf3f. Workflow now asserts immutable event checkout before installation/deploy.
