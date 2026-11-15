# SONORA — AI Music Studio

SONORA is an evolving AI-powered music creation platform built around open-weight music generation models, on-demand GPU infrastructure, and agent-compatible workflows.

The project began with a working ACE-Step 1.5 deployment on Modal: a lightweight CPU-hosted web portal submits asynchronous generation jobs to a separate GPU worker, which generates MP3 audio and stores outputs in persistent storage. SONORA will grow this foundation into a full music studio with a persistent library, richer creative controls, and reliable APIs for both people and AI agents.

> **Project status:** Early development. The current ACE-Step + Modal generation path is working; the broader SONORA product roadmap is planned and will be implemented incrementally.

## Current capabilities

- Browser-based music prompt submission.
- Optional lyrics input and configurable duration.
- ACE-Step 1.5 generation through a Modal GPU worker.
- Asynchronous job submission and status polling.
- MP3 output stored in a persistent Modal Volume.
- Password-protected generation endpoint.
- Separate CPU web service and on-demand GPU worker.

See [docs/architecture.md](docs/architecture.md) for the current system and planned evolution.

## Product direction

1. **Create:** Generate instrumental and vocal music from prompts and lyrics.
2. **Control:** Guide genre, mood, tempo, language, instrumentation, vocals, and duration.
3. **Library:** Keep generated tracks searchable, playable, and manageable.
4. **Iterate:** Create variations and manage versions of a track.
5. **Automate:** Expose stable APIs so an AI agent can create, monitor, evaluate, and retrieve tracks.
6. **Operate efficiently:** Start GPU compute only when needed, measure generation performance, and control cost.

## Architecture direction

- **Music model:** ACE-Step 1.5 (initial engine).
- **GPU execution:** Modal on-demand GPU functions.
- **Current application service:** Python + FastAPI on Modal CPU compute.
- **Planned frontend:** React / Next.js.
- **Planned durable application database:** PostgreSQL.
- **Current audio storage:** Modal Volume.
- **Version control:** GitHub.

## Repository structure

```text
sonora-ai-music-studio/
├── backend/          # Application API and domain services (planned)
├── frontend/         # Studio user interface (planned)
├── music-engine/     # Model integration and generation workers
├── docs/             # Architecture, roadmap, and engineering notes
├── tests/            # Automated tests
├── .gitignore
├── LICENSE
└── README.md
```

## Development principles

- Preserve the working music-generation path while building the new product.
- Keep secrets, passwords, tokens, and private configuration out of source control.
- Separate web/API workloads from GPU inference workloads.
- Use durable records for jobs and tracks; do not rely on process memory.
- Make agent-facing operations explicit, validated, and observable.
- Test changes before deploying them to the live service.
- Document what is implemented separately from what is planned.

## Getting started

The current deployed Modal implementation is being migrated into this repository after source review and configuration sanitization. Deployment instructions will be added after the source is organized and secrets are externalized.

Never commit live credentials or the current studio password. Use Modal Secrets or environment variables for sensitive configuration.

## Roadmap

See [docs/roadmap.md](docs/roadmap.md).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Acknowledgements

SONORA builds on the open-source [ACE-Step 1.5](https://github.com/ACE-Step/ACE-Step-1.5) project. Model and dependency licenses remain those of their respective owners; review them before redistributing models or generated assets.
