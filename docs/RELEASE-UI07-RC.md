# UI01-07 release candidate

Validated source commit: `b514fa3688c7f2d31b08965cc2b68ff88d9a110c`. Source tree: `f579ec16771e457c38e46fdd715057f70f428595`.
Feature branch: `feat/phase-1-library`. Main remains `1c059b7e66f8a85cee2d5e1cbebb3ad0339cd3a7`, checked against the remote during this review.

UI05 adds versioned local backup/restore, UI06 adds arrangement intentions, UI07 adds exact prompt budgeting and explicit inclusion controls. All source increment history is retained locally. GitHub UI publication creates different commit identities; exact tree equality, not a substituted commit identity, is the publication criterion. A subsequent deployment must separately record its actual event and checkout SHA.

## Isolated verification

- Fresh isolated worktree at the validated SHA, npm ci, 37 Node tests, production audit zero, clean reproducible build and byte-identical committed assets.
- PowerShell runner: 135 Python tests plus compile checks on Python3.10 and3.12.
- 3,072 exhaustive compiler/ledger cases: inclusion/reference flags, arrangement modes, sparse/populated data and vocal modes. Exact Unicode codepoint counts and component sums. Manual payload isolation into exactly prompt/lyrics/duration.
- Schema1/2/3 Merge/Replace idempotence matrix;30 hostile-key placements rejected; schema4/malformed/oversized rejected. Browser suite verifies quota atomicity, cancelled Replace, persistence, collision remapping, blocked storage and default Merge.
- Cross-language API validation30 cases; public security65 route/method/traversal cases including OPTIONS; zero worker callbacks.
- UI02-07 regression and clean-profile keyboard/modal suites:1440/1024/768/390/320. No JS errors, external requests or mutation requests.
- Dynamic counts: polite/atomic700ms debounce, actual manual and assembled counts distinct. Keyboard all inclusions/select, visible focus, reduced motion and bottom-of-panel320px checked.
- 200%-equivalent reflow at720x500 and390x844 checked and pixels inspected. Browser chrome zoom itself was not driven. No real screen-reader audio test; do not claim full WCAG certification.

## Asset SHA256

- `backend/sonora/static/index.html`: `efba05889b8c363d9206926c3705eec0f6c05ff580cb300dfdc056ef834f0d66`
- `backend/sonora/static/assets/app.js`: `1aa5c1ce4a6e99c9c2a9ee8d830df1ced3e9e68897023254b0aec486d07616cd`
- `backend/sonora/static/assets/app.css`: `987ca8e925655b68233d9b4bc4c7a3e45c612c2c04b3cf9de03e86ce8d6c7aae`

## Safety and release scope

No backend/API/Modal/GPU/model changes in UI05-07. Generation stays hard OFF in the public preview; empty library is real. No seed tracks, simulated accounts or invented credits. Local QA does not call Modal. Current live UI04 preview remains untouched during publication.

Publication is not deployment. Deployment needs a new immutable claim, fresh usage check, temporary credentials, bounded CPU-only attempt, live acceptance and credential cleanup. Preserve all prior claim records. Displayed usage stop$4.50; absolute cap$5.00, noGPU/model downloads/card charges. Most recently observed usage before this release preparation was1.6930411857387493 credits usage, not a fresh deployment-time meter.

The manifest's source commit precedes this documentation-only commit. Verify exact complete-tree equality at the final publication/deployment revisions, including this manifest and any separately reviewed deployment-claim update.

## Actual browser zoom follow-up

Actual headed Chromium200% page zoom PASS. Clean temporary bundled Chromium profile, minimal local QA-only extension with tabs permission, `chrome.tabs.setZoom`2.0 in automatic/per-tab mode. Asserted getZoom2.0 plus actual DPR1→2 and CSS viewport1439x999→719x499. Keyboard inclusion/select, exact manual editing and rebuild-cancel passed with no horizontal overflow. Captured and inspected actual display pixels, including visible200% browser popover. No application/CSS changes required. Manual zoom mode was rejected as a false positive because it returns2.0 without changing DPR/layout; only the verified automatic-mode result counts.

Real screen-reader audio smoke test: NOT RUN. Windows/NVDA unavailable. Semantics, accessible names/states, keyboard focus and debounced status content were tested; this is not a full accessibility certification.

Browser editor normalization produced a harmless final-brace indentation difference in WorkspaceBackupPanel.jsx. Local commit00cac94 preserves that exact remote whitespace and remote/local complete trees matchedccec737c172fada04dc0f1b77b905ea3439d5640 before this documentation update. Compiled assets and all application behavior are unchanged from the isolated validated source.
