# SONORA Roadmap

This roadmap distinguishes current foundations from planned work. A milestone is complete only after implementation and verification.

## Verified overnight status (2026-11-16)

- UI01-07 live as an isolated read-only creative-studio preview. Exact deployment/cost evidence: RELEASE-UI07-LIVE.md.
- UI08 optional first-session guide implemented and tested locally, not yet published or deployed.
- Current local suite39 frontend tests,135Python tests on3.10/3.12, contract30, public security65 plus responsive browser suites. These do not prove real GPU generation.
- Guarded backend foundations exist, but no-GPU audit found enqueue uncertainty, duplicate worker-entry and preterminal library-visibility gaps. No production readiness or first real SONORA generation is claimed.
- Music generation remains hardOFF. First real model run requires separately scoped approval and current cost/termination/storage checks. No refund or charging system exists.

The original milestone checklist below is a planning baseline, not the current task list. Some pieces are now implemented locally; completion still requires the stated exit criteria and real end-to-end evidence.

## Milestone 0 — Repository and baseline

- [x] Create public GitHub repository.
- [x] Document the current working architecture.
- [ ] Add current Modal source files after reviewing and sanitizing configuration.
- [ ] Add reproducible local development and deployment instructions.
- [ ] Capture a baseline generation run and timing measurements.
- [ ] Add tests for input validation and job lifecycle.

**Exit criteria:** the deployed baseline is represented in version control without secrets, and another developer can understand how it works.

## Milestone 1 — Reliable generation backend

- [ ] Separate API schemas, job service, and model worker.
- [ ] Persist complete job metadata and explicit state transitions.
- [ ] Handle worker failures, timeouts, retries, and orphaned jobs.
- [ ] Confirm audio Volume commit/reload behavior.
- [ ] Verify GPU scale-to-zero and CPU/GPU cost boundaries.
- [ ] Measure cold starts and generation time.

**Exit criteria:** generation jobs can be submitted, tracked, recovered, and downloaded reliably.

## Milestone 2 — SONORA Studio experience

- [ ] Build a polished studio interface.
- [ ] Add controls for instrumental/vocal mode, language, style, mood, tempo, and duration where supported by the engine.
- [ ] Add an in-browser audio player.
- [ ] Add a persistent track library with search and filters.
- [ ] Add track naming, favorites, and metadata display.

**Exit criteria:** users can create, listen to, find, and manage their generated tracks.

## Milestone 3 — Creation and iteration

- [ ] Add prompt improvement assistance.
- [ ] Add lyrics assistance and language-aware generation.
- [ ] Add track variations and version relationships.
- [ ] Evaluate engine support before exposing extend, remix, cover, repaint, or stems.
- [ ] Add generation history and reproducibility metadata.

**Exit criteria:** users can iterate on creative results and understand how each version was produced.

## Milestone 4 — Agentic workflows

- [ ] Define a versioned agent-facing API.
- [ ] Support create-job, poll-job, list-tracks, and download-track operations.
- [ ] Add scoped authentication and usage limits.
- [ ] Create an agent integration guide and example workflow.
- [ ] Test browser automation and direct API workflows.

**Exit criteria:** an external agent can safely perform a complete music-generation workflow through documented interfaces.

## Milestone 5 — Production readiness and portfolio

- [ ] Add automated tests and CI.
- [ ] Add structured logs, metrics, and alerting.
- [ ] Add usage and cost reporting.
- [ ] Review security, privacy, retention, and licensing.
- [ ] Publish architecture diagrams and deployment documentation.
- [ ] Record a demo and write an engineering case study.
- [ ] Publish measured results rather than estimated or invented performance claims.

**Exit criteria:** a publicly demonstrable, documented project with reproducible evidence of its architecture and performance.
