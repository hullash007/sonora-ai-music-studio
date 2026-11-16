# SONORA by Hullash - self-hosted alpha

Download Sonora, connect your own music engine, choose an available model, and create music with your own hardware or provider balance. No Google account with the app owner, shared quota, owner API key or prepared owner image is required.

**Status: 0.1.0a1, experimental.** The installable studio and ACE-Step protocol are covered by local/mock tests. The new pinned GPU bootstrap has not yet completed a real GPU generation test. Do not treat installation, model discovery, or checksum verification as proof of working music inference. This is separate from the hosted portfolio demo, which is unchanged.

## Install the studio

Use Python 3.11 or 3.12. The tested client environment is Linux x86_64 with Python 3.12; Windows/macOS client use is not separately verified yet. No Node.js, Modal SDK, torch or GPU dependency is needed to install the studio itself.

Download this repository's source ZIP and extract it, or clone the repository. Open a terminal in that folder:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install .
sonora --help
```

A built `sonora_selfhost-0.1.0a1-py3-none-any.whl` can instead be installed with `python -m pip install /path/to/the-wheel.whl`. There is no PyPI publication yet. Do not assume `pip install sonora` installs this project.

## Connect an engine you own

Sonora currently supports **official ACE-Step 1.5 REST**, not arbitrary music APIs or chat-completion endpoints. Run your own local ACE-Step server, use the experimental pinned bootstrap below, or connect a remote ACE-Step endpoint you own. For independently installed engines, consult [upstream installation](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/INSTALL.md).

```sh
sonora setup
sonora doctor
sonora start
```

Setup asks for the server's origin URL, an optional hidden API key and a model reported by that server. A local default is `http://127.0.0.1:8001`. Remote servers must use HTTPS; path-prefixed reverse proxies are not supported in this alpha. For a local server without authentication:

```sh
sonora setup --endpoint http://127.0.0.1:8001 --no-key
```

For remote access, use an authenticated TLS endpoint under your control. Never expose an unauthenticated ACE-Step server to the internet. An SSH tunnel that terminates at a loopback endpoint is another option you can manage yourself. Sonora does not create a cloud account, provision GPUs, grant credits, or change your provider billing limits.

Setup and doctor only discover models. They do not submit a task, initialize a new model, or verify your available credits. Failed setup keeps the previous configuration. There is no silent test generation.

`sonora start` prints a private loopback URL with a one-session access token. Open it on the same computer. Do not share it or expose the local port through a tunnel. Credentials stay on the Python side, not in the browser. The browser uses a short-lived process session cookie; restart the server for a new token.

### Make the first real track

In the browser, select an available music model, describe a track, choose 10-120 seconds, confirm use of your own hardware/balance, then click Generate. This is the explicit inference test. One click requests `batch_size=1`, PCM WAV and text-to-music without language-model planning. No waveform fixture or fake generation adapter exists in this distribution.

Model selection means models your server reports available and loaded. Sonora does not accept arbitrary model IDs or silently call `/v1/init`. You can manage additional supported models on your own ACE-Step server, then refresh the connection. The experimental bootstrap below currently pins one Turbo model; it does not install every model choice.

The alpha downloads and checks a complete PCM WAV, confirms its actual duration is within two seconds of the request, and saves it to the local library. It does not judge musical quality, copyright, pronunciation or lyrical accuracy. Only combinations actually tested with a real server should be called validated.

## Experimental own-GPU bootstrap

This path is executable code, but **not yet GPU-validated**. It targets Linux x86_64 with an NVIDIA CUDA GPU. Other hardware can use independently installed upstream ACE-Step and connect through the same client, but is not a tested Sonora bootstrap target.

Prerequisites you install yourself: Git, ffmpeg, a working NVIDIA driver with `nvidia-smi`, and `uv 0.13.5`. Sonora does not install system packages or change your drivers. Upstream documents at least 4 GB VRAM for DiT-only mode, but that is an upstream claim, not a tested guarantee here. This alpha disables the optional LM. Allow at least 30 GiB free before installation; core model files alone total about 10.1 GB decimal. Dependencies and caches need more space, and slow networks may take a long time.

```sh
sonora engine plan
# Read the plan and upstream model/code licenses before proceeding.
sonora engine install --accept-downloads
sonora engine verify
sonora engine start
```

Install fetches a fixed upstream Git revision, installs from its dependency lock using Python 3.12, downloads a fixed Hugging Face model revision, and checks every model file's SHA256 or Git blob hash from `upstream-lock.json`. Dependencies and model code are third-party software. The checkpoint repository includes Python model definitions. Installation does not prove those dependencies are bug-free or constitute a license/data audit. Sonora's MIT license does not replace third-party terms.

The source pin is `ca1e85fe9430179831e6bc6be790c332190a3866`; model repository `ACE-Step/Ace-Step1.5` is pinned to `19671f406d603126926c1b7e2adc169acbcade22`. Current upstream installation and API documents were checked on 16 November 2026. Do not use an unreviewed branch update as a substitute for a tested release.

Engine start verifies source/model files again, forces loopback binding, selects Turbo/DiT-only CUDA, and requests offline Hugging Face/Transformers loading. It loads the GPU but does not submit music. Stop it with Ctrl+C when finished. **If this computer is a cloud GPU VM, its provider may bill the machine while it is running, even with no generation task.** Sonora neither provisions nor tears down that VM. Set budgets and shut it down with your provider.

Keep the engine terminal running. In a second terminal, activate the studio environment, run `sonora setup --endpoint http://127.0.0.1:8001 --no-key`, then `sonora start`. Generate a short test explicitly. If model loading fails, installation is incomplete, not a working GPU setup. Review the upstream error; do not keep retrying billable tasks.

## Persistence, keys and recovery

Data lives outside the repository:

- Linux/macOS default: `$XDG_DATA_HOME/sonora` or `~/.local/share/sonora`.
- Windows default: `%LOCALAPPDATA%\Sonora`.
- Override locally with `SONORA_HOME` before setup and start. Do not use a shared directory.

`config.json` contains the endpoint, model and a credential reference. A separate local credential file contains the key. The directory is mode 0700 and new secret files 0600 on POSIX. This is local file protection, **not encryption at rest**. On Windows, use a private NTFS user profile and its access controls; POSIX modes are not a Windows security guarantee. `SONORA_API_KEY` can supply a key from your own environment instead of saving it. Never send keys in chat, put keys in URLs/command arguments, or commit your data directory. Backups of this directory include private prompts, audio and saved credentials.

The SQLite job log and WAV library persist across restarts. The same data directory cannot run two studios. Job submission is durably claimed before the network request. One outstanding or uncertain job blocks further work, and a browser request ID prevents duplicate clicks from creating duplicate jobs.

If the submission request times out or Sonora stops before saving the remote ID, **the task may already be billable**. Sonora marks it uncertain and never auto-retries. If a remote ID was saved, restart resumes tracking that ID, not submission. A result is not marked complete until downloaded audio is checked and saved. A failed download does not submit another music task.

```sh
# Stop the studio before changing/reconciling state.
sonora jobs
sonora reconcile JOB_ID
sonora start
```

`reconcile` requires the same endpoint and a known remote ID. It resumes retrieval only. Tracking pauses after an hour. If the provider has lost the task, or a submission ID was never received, inspect your provider first. After you have independently checked the outcome and accepted any provider cost, `sonora acknowledge JOB_ID --remote-outcome-checked` releases the blocked local admission. It does **not** cancel, delete or refund any remote task and does not assert that compute stopped. There is no remote cancellation feature in this alpha.

Uninstall the client with `python -m pip uninstall sonora-selfhost`. Your library and engine remain intact. Back up wanted tracks and stop both processes before manually removing their data directory. An upgrade can be rolled back by reinstalling the prior wheel; this version has only schema version 1. Back up the data directory before future schema-changing upgrades.

## Verification scope and next gate

Free validation covers clean wheel installation, CLI flow, mock ACE-Step protocol, real local HTTP/browser interaction, WAV decoding, state recovery, security boundaries and packaged asset inspection. Test fixtures are synthetic protocol/audio fixtures, not generated music.

Still required before claiming a working end-to-end GPU route: a clean pinned engine install on a declared GPU/driver, successful real model generation, decoded output/playback and restart checks. Any agent-run paid test requires separate user approval. Own-Modal provisioning and additional provider adapters are not implemented. No free-credit offer is promised.

Developer tests:

```sh
python -m pip install -r requirements-dev.txt pytest build
PYTHONPATH=backend python -m unittest discover -s tests
python -m pytest tests/selfhost -q
python -m build
```

Official protocol reference: [pinned ACE-Step API](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/docs/en/API.md). Model/license declarations: [Hugging Face model](https://huggingface.co/ACE-Step/Ace-Step1.5/tree/19671f406d603126926c1b7e2adc169acbcade22), [upstream code license](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE).
