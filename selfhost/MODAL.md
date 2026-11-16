# Sonora with your own Modal account

Experimental source candidate, not a validated cloud music release. Cloud CPU preparation, pinned dependencies, all model hashes and persistence passed on November 16, 2026. A GPU request did not receive hardware before its queue cutoff. Real GPU installation, music output and this downloader workflow remain unverified. Do not describe model discovery as successful inference.

This route uses your own Modal workspace, compute balance and storage. Nothing connects to Hullash's account, keys, hosted demo, resource IDs or audio. No free credits are promised. The separate hosted demo is unchanged.

## Install and sign in

Download this release's wheel. In a Python 3.11 or 3.12 environment:

```sh
python -m pip install './sonora_selfhost-0.2.0a1-py3-none-any.whl[modal]'
sonora modal plan
sonora modal login
python -m modal token info
```

Create a Modal account on Modal's own site if needed. `login` opens Modal's normal account authentication. Never send your Modal password or token to the app owner. The Modal SDK manages your credentials. Check the workspace reported by `token info`; the next commands require its exact name. Switching profiles must happen through Modal's own SDK before starting Sonora work.

Review your own Modal billing limits and current prices. Image builds, downloads, GPU loading and idle time can incur charges, even if no track is produced. Storage can remain billable after stopping compute, and deletion/billing can lag. Runtime limits are not dollar caps. This experimental route does not change billing limits or add a payment method.

## Prepare the full engine, without a GPU

Read `sonora engine plan` and the source/model terms linked by that plan and the installed package README first. Replace `YOUR_WORKSPACE` with your verified workspace username:

```sh
sonora modal prepare --workspace YOUR_WORKSPACE --accept-compute --accept-downloads
sonora modal status
```

The full pinned upstream dependency lock and all 28 checkpoint files are used, not a smaller substitute. Preparation uses at most one CPU container, 2 cores and 8 GiB RAM, with 20 minutes execution and 120 seconds startup. Queue admission expires after ten minutes. The base image build happens separately and is not covered by that function execution timeout. Retained prepared data is checked against 24 GiB; this is not a hard peak storage quota. No automatic music task or automatic retry is submitted.

Keep checking status and inspect your Modal dashboard. A failed or unclear preparation is not ready. Do not rerun against an uncertain state. Stop and reconcile the existing resources first.

## Start one capped GPU session

After preparation reports `prepared`:

```sh
sonora modal start --workspace YOUR_WORKSPACE --accept-compute
sonora modal status
```

This explicitly requests one L4 with 4 cores and 32 GiB RAM. Queue admission expires after ten minutes. Once allocated, the session has 24 minutes for checksum verification, the actual shipped installer, model loading and all user work, plus 120 seconds startup. Installation/loading consume that window. The server can finish or fail sooner. No music is generated automatically. The request is not retried automatically.

The remote function reloads the prepared Volume, compares its marker with the committed confirmation, verifies the full pinned source/model content, runs Sonora's shipped GPU installer, then starts an authenticated server. A random session key is stored in your private local Sonora data directory and passed as a remote function argument to your own Modal session. It exists in that remote process while serving; do not share provider logs or invocation records containing secrets. The tunnel uses HTTPS; knowing its URL is not sufficient to authorize requests. The cloud endpoint is short-lived and should never be shared.

When status reports ready:

```sh
sonora modal connect
sonora start
```

Open the local studio URL printed by `sonora start` on the same computer. Select an available supported model, enter a prompt and explicitly click Generate with your own-compute confirmation. The bootstrap installs Turbo; it does not promise arbitrary model support. A complete, playable generated WAV is the real inference test.

`connect` only checks model discovery and saves the endpoint/key. It does not generate music, check credits or prove that the GPU can produce a track. Downloads and model hashes alone are not that proof either.

## Stop and check the bill

Closing the browser or local studio does not stop the remote session or undo a submitted generation. The server-side session timeout is a backstop, not a reason to leave it running.

```sh
sonora modal stop --delete-models
```

Stop cancels known calls, stops all apps in this locally recorded unique Sonora session namespace, reads actual stopped/zero-task state, then removes this session's model Volume and state dictionary when `--delete-models` is supplied. It will not delete the local music library. Without `--delete-models`, model storage remains and may keep accruing charges. Check Modal's own dashboard and bill after stopping. If stop cannot verify termination, keep `modal-state.json` and inspect the dashboard; do not delete the handles or start another session.

After successful stop and model deletion, `sonora modal reset` re-checks current app/resource cleanup and archives the old local session handles before allowing a new preparation. It does not delete your music library. A new preparation is a new explicit compute decision, never an automatic retry.

Sonora never cancels unrelated apps or deletes pre-existing model storage. Do not manually edit the session namespace or copy state between Modal workspaces.

## Recovery and limits

Local session handles live beside the existing Sonora library in `modal-state.json`. The session key has a separate private file. As with other local credentials, POSIX permissions are not encryption; protect your computer and backups. Modal account credentials remain with the SDK, not in browser responses or Sonora's session state.

If a generation submission is uncertain, the existing job log blocks resubmission. Stop the session or inspect your own provider job before acknowledging it. Neither a local error nor a queue timeout proves that all remote work stopped. `sonora jobs` and the recovery commands in the package README preserve the original remote job rather than generating again.

This candidate has offline control tests. It still needs independent review, clean-wheel inspection and a real own-account GPU music test before it can be called working end to end. The user-visible Modal route has not been exercised against a fresh downloader account.

### Bounded manual stop recovery

If a transient transport failure interrupts stop, keep the state file, check your own dashboard and rerun `sonora modal stop --delete-models`. This retries cancellation/stop/deletion of the same recorded resources; it never launches new compute. Do not reset or delete handles while termination is uncertain.

The SDK app list may eventually omit old stopped apps. An absent app is not treated as stopped. If recorded apps have aged out and Sonora cannot verify them, automatic cleanup/reset deliberately refuses. In your own Modal dashboard, check the exact recorded app IDs and this session namespace, stop any remaining containers, and remove only its exact Volume/dictionary after you have confirmed termination. If the dashboard also cannot establish state, contact Modal support with those IDs. Preserve the state file for that recovery. This candidate offers no command to bypass unverified termination. Do not start new work as a recovery shortcut.

Status never expires a successfully completed preparation or finished serving phase. After an explicit stop without deletion, status reports retained storage and does not delete it. Retained models remain billable until an explicit stop with `--delete-models`; a stopped session cannot be restarted without that cleanup/reset in this candidate.
