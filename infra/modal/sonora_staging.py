"""SONORA isolated staging app.

Public preview generation is unconditionally disabled. The run-config sentinel
is never created or modified by this module.

Do not deploy or invoke this app without separate approval.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import modal


APP_NAME = "sonora-staging"

PROJECT_DIR = "/opt/ACE-Step-1.5"
CHECKPOINT_DIR = f"{PROJECT_DIR}/checkpoints"
AUDIO_DIR = "/mnt/sonora-staging-audio"
BACKEND_DIR = "/opt/sonora/backend"

ACE_STEP_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"

VALIDATION_DURATION_SECONDS = 30
WORKER_STARTUP_TIMEOUT_SECONDS = 600
WORKER_EXECUTION_TIMEOUT_SECONDS = 1200

app = modal.App(APP_NAME)

# Staging-only storage. Volume v2 is explicit; it is experimental in the
# current Modal SDK. Existing V1 volumes must not be silently reused.
checkpoints_volume = modal.Volume.from_name(
    "sonora-staging-checkpoints",
    create_if_missing=True,
    version=2,
)
audio_volume = modal.Volume.from_name(
    "sonora-staging-audio",
    create_if_missing=True,
    version=2,
)

# Job state is staging-only. The guard Dict must be provisioned separately.
job_store = modal.Dict.from_name(
    "sonora-staging-jobs",
    create_if_missing=True,
)
guard_store = modal.Dict.from_name(
    "sonora-staging-guard",
    create_if_missing=False,
)

portal_secret = modal.Secret.from_name("sonora-staging-portal")

# Deploy-source discovery must work when Modal flattens this module to /root.
REMOTE_BACKEND_DIR = Path(BACKEND_DIR)


def _find_local_backend_dir(module_file: str | Path) -> Path | None:
    for root in Path(module_file).resolve().parents:
        candidate = root / "backend"
        if (candidate / "sonora" / "library.py").is_file():
            return candidate
    return None


LOCAL_BACKEND_DIR = _find_local_backend_dir(__file__)
BACKEND_IMPORT_DIR = LOCAL_BACKEND_DIR or REMOTE_BACKEND_DIR
if not (BACKEND_IMPORT_DIR / "sonora" / "library.py").is_file():
    raise RuntimeError(
        "SONORA backend not found in local repository or /opt/sonora/backend"
    )
if str(BACKEND_IMPORT_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_IMPORT_DIR))

portal_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi==0.162.5")
)

music_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libsndfile1", "libgomp1")
    .pip_install("uv")
    .run_commands(
        f"git clone https://github.com/ACE-Step/ACE-Step-1.5.git {PROJECT_DIR} "
        f"&& cd {PROJECT_DIR} && git checkout {ACE_STEP_COMMIT}"
    )
    .run_commands(
        f"cd {PROJECT_DIR} && uv sync --no-dev --python 3.12"
    )
)


# These files are packaged locally into the image. A remote import must not
# attempt to discover or upload host paths again.
if LOCAL_BACKEND_DIR is not None:
    portal_image = portal_image.add_local_dir(str(LOCAL_BACKEND_DIR), remote_path=BACKEND_DIR)
    music_image = music_image.add_local_dir(str(LOCAL_BACKEND_DIR), remote_path=BACKEND_DIR)


@app.function(
    image=music_image,
    gpu=["A10", "L4"],
    cpu=(8, 8),
    memory=(32768, 32768),
    timeout=WORKER_EXECUTION_TIMEOUT_SECONDS,
    startup_timeout=WORKER_STARTUP_TIMEOUT_SECONDS,
    retries=0,
    single_use_containers=True,
    min_containers=0,
    max_containers=1,
    scaledown_window=30,
    volumes={
        f"{PROJECT_DIR}/checkpoints": checkpoints_volume,
        AUDIO_DIR: audio_volume,
    },
)
def generate_track(
    job_id: str,
    prompt: str,
    lyrics: str,
    duration: int,
) -> dict[str, Any]:
    """Generate one 30-second track under the durable staging guard."""
    if duration != VALIDATION_DURATION_SECONDS:
        raise ValueError(
            f"Validation worker permits exactly "
            f"{VALIDATION_DURATION_SECONDS} seconds"
        )

    sys.path.insert(0, BACKEND_DIR)
    sys.path.insert(0, PROJECT_DIR)

    from sonora.guard import (
        read_run_config_identity,
        read_terminal_state,
    )
    from sonora.worker import (
        GenerationStopped,
        assert_job_active,
        run_guarded_generation,
    )

    # Identity-only reader: no admission-expiry check is applied to this
    # already-admitted job. read_terminal_state still verifies the admission.
    run_config = read_run_config_identity(guard_store)

    initial_job = job_store.get(job_id)
    if not isinstance(initial_job, dict):
        raise RuntimeError("Staging job record is missing")

    stored_config = initial_job.get("_guard_config")
    if not isinstance(stored_config, dict):
        raise RuntimeError("Job does not contain its guard run identity")

    expected_config = {
        "run_id": run_config.run_id,
        "issued_at": run_config.issued_at,
        "expires_at": run_config.expires_at,
        "max_attempts": run_config.max_attempts,
    }
    if stored_config != expected_config:
        raise RuntimeError("Job guard identity does not match the run sentinel")

    created_at = float(initial_job.get("created_at", time.time()))

    def update_progress(stage: str) -> None:
        """Update ordinary progress without overwriting guard identity/state."""
        state = read_terminal_state(guard_store, run_config, job_id)
        if state is not None:
            raise GenerationStopped(state["state"])

        current = job_store.get(job_id)
        if not isinstance(current, dict):
            raise RuntimeError("Staging job record disappeared")

        updated = dict(current)
        updated["status"] = "running"
        updated["stage"] = stage
        updated["error"] = None
        # Preserve _guard_config and all previously stored fields.
        job_store[job_id] = updated

    def generate_audio() -> str:
        """Load the pinned model and synchronously generate the audio file."""
        update_progress("Checking staging generation state")
        assert_job_active(guard_store, run_config, job_id)

        site_packages = (
            Path(PROJECT_DIR)
            / ".venv"
            / "lib"
            / "python3.12"
            / "site-packages"
        )
        sys.path.insert(0, str(site_packages))

        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("PYTHONUNBUFFERED", "1")

        update_progress("Preparing isolated staging checkpoint directory")
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)

        # Check before checkpoint/model initialization. If cancellation won
        # while the function was starting, do not begin expensive model work.
        assert_job_active(guard_store, run_config, job_id)

        update_progress("Downloading or loading ACE-Step checkpoint")
        from acestep.api.model_download import ensure_model_downloaded
        from acestep.handler import AceStepHandler
        from acestep.inference import (
            GenerationConfig,
            GenerationParams,
            generate_music,
        )
        from acestep.llm_inference import LLMHandler

        ensure_model_downloaded(
            "acestep-5Hz-lm-1.7B",
            CHECKPOINT_DIR,
        )

        assert_job_active(guard_store, run_config, job_id)

        update_progress("Initializing ACE-Step DiT model")
        dit_handler = AceStepHandler()
        dit_message, dit_ok = dit_handler.initialize_service(
            project_root=PROJECT_DIR,
            config_path="acestep-v15-turbo",
            device="cuda",
            use_flash_attention=True,
            compile_model=False,
            offload_to_cpu=False,
            offload_dit_to_cpu=False,
        )
        if not dit_ok:
            raise RuntimeError(
                f"ACE-Step DiT initialization failed: {dit_message}"
            )

        assert_job_active(guard_store, run_config, job_id)

        update_progress("Initializing ACE-Step language model")
        llm_handler = LLMHandler()
        llm_message, llm_ok = llm_handler.initialize(
            checkpoint_dir=CHECKPOINT_DIR,
            lm_model_path="acestep-5Hz-lm-1.7B",
            backend="vllm",
            device="cuda",
            offload_to_cpu=False,
            dtype=None,
        )
        if not llm_ok:
            raise RuntimeError(
                f"ACE-Step language-model initialization failed: {llm_message}"
            )

        # Final check directly before starting the actual generation call.
        assert_job_active(guard_store, run_config, job_id)

        job_dir = os.path.join(AUDIO_DIR, "jobs", job_id)
        os.makedirs(job_dir, exist_ok=True)

        update_progress("Generating 30-second validation track")

        params = GenerationParams(
            task_type="text2music",
            caption=prompt,
            lyrics=lyrics if lyrics.strip() else "[Instrumental]",
            duration=float(VALIDATION_DURATION_SECONDS),
            vocal_language="unknown",
            thinking=True,
            use_cot_metas=True,
            use_cot_caption=True,
            use_cot_language=True,
            inference_steps=8,
            shift=3.0,
        )

        generation_config = GenerationConfig(
            batch_size=1,
            allow_lm_batch=False,
            use_random_seed=True,
            audio_format="mp3",
            mp3_bitrate="128k",
            mp3_sample_rate=48000,
        )

        result = generate_music(
            dit_handler,
            llm_handler,
            params,
            generation_config,
            save_dir=job_dir,
        )

        if not result.success:
            raise RuntimeError(
                result.error
                or result.status_message
                or "ACE-Step generation failed"
            )

        if not result.audios:
            raise RuntimeError(
                "ACE-Step completed without returning an audio file"
            )

        output_path = result.audios[0].get("path")
        if not output_path:
            raise RuntimeError("ACE-Step returned an empty audio path")

        resolved_output = os.path.abspath(output_path)
        resolved_job_dir = os.path.abspath(job_dir)

        if os.path.commonpath(
            [resolved_output, resolved_job_dir]
        ) != resolved_job_dir:
            raise RuntimeError(
                "Generated audio path is outside this job's directory"
            )

        if not os.path.isfile(resolved_output):
            raise RuntimeError(
                f"Generated audio file was not found: {resolved_output}"
            )

        return resolved_output

    result = run_guarded_generation(
        guard_store=guard_store,
        run_config=run_config,
        job_id=job_id,
        audio_root=AUDIO_DIR,
        prompt=prompt,
        lyrics=lyrics,
        duration_seconds=VALIDATION_DURATION_SECONDS,
        generate_audio=generate_audio,
        commit_audio=audio_volume.commit,
        engine="ACE-Step 1.5",
        model_revision=ACE_STEP_COMMIT,
        created_at=created_at,
    )

    # Ordinary job state is only a display/cache. Guard state remains
    # authoritative in the HTTP status endpoint.
    if result.get("status") in {"complete", "failed"}:
        current = job_store.get(job_id)
        if isinstance(current, dict):
            updated = dict(current)
            updated["status"] = result["status"]
            updated["stage"] = (
                "Music generation complete"
                if result["status"] == "complete"
                else "Generation failed"
            )
            updated["error"] = result.get("error")
            if result.get("published"):
                updated.update(
                    file=result["file"],
                    filename=result["filename"],
                    title=result["title"],
                    duration_seconds=result["duration_seconds"],
                    completed_at=result["completed_at"],
                )
            job_store[job_id] = updated

    return result


@app.function(
    image=portal_image,
    cpu=(1, 1),
    memory=(1024, 1024),
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    volumes={AUDIO_DIR: audio_volume},
    secrets=[portal_secret],
)
@modal.asgi_app()
def music_portal():
    """Expose the unchanged studio UI through the injected HTTP application."""
    sys.path.insert(0, BACKEND_DIR)

    from sonora.guard import read_run_config_identity
    from sonora.worker import cleanup_confirmed_cancelled_job
    from sonora.web import create_app

    # The public preview cannot dispatch a generation, even if a Secret changes.
    generation_enabled = False  # Public preview is read-only, regardless of Secret.

    def enqueue_generation(
        job_id: str,
        prompt: str,
        lyrics: str,
        duration: int,
    ):
        if not generation_enabled:
            raise RuntimeError("Staging generation is disabled")
        if duration != VALIDATION_DURATION_SECONDS:
            raise ValueError("Only the 30-second validation duration is enabled")

        # Return the FunctionCall. The HTTP layer persists its object_id.
        return generate_track.spawn(
            job_id,
            prompt,
            lyrics,
            VALIDATION_DURATION_SECONDS,
        )

    def cancel_generation(call_id: str) -> bool:
        """Request cancellation; this does not claim it has terminated."""
        call = modal.FunctionCall.from_id(call_id)
        call.cancel(terminate_containers=True)
        return True

    def call_result_state(call_id: str) -> str:
        """Conservative observation. Unknown exceptions never prove stop."""
        call = modal.FunctionCall.from_id(call_id)
        try:
            call.get(timeout=0)
            return "returned"
        except modal.exception.FunctionTimeoutError:
            return "execution_timed_out"
        except modal.exception.InputCancellation:
            return "input_cancelled"
        except modal.exception.TimeoutError:
            return "pending"
        except Exception:
            # get() can rethrow arbitrary remote exceptions, but an SDK/RPC or
            # deserialization problem can also occur. Preserve ambiguity.
            return "unknown"

    def generation_termination_confirmed(call_id: str) -> dict[str, Any]:
        """Same-call outcome AND worker inactivity, never cancellation by inference."""
        state = call_result_state(call_id)
        evidence = {"call_id": call_id, "state": state,
                    "termination_confirmed": False, "zero_activity": False}
        if state not in {"returned", "execution_timed_out", "input_cancelled"}:
            return evidence
        try:
            stats = generate_track.get_current_stats()
            zero = (stats.backlog == 0 and stats.num_running_inputs == 0
                    and stats.num_total_runners == 0)
        except Exception:
            return evidence
        evidence.update(zero_activity=zero, termination_confirmed=zero)
        return evidence

    def cleanup_cancelled_generation(
        job_id: str,
        run_id: str,
        claim_token: str,
    ) -> tuple[str, ...]:
        """Clean only after the HTTP layer has persisted confirmed cancellation."""
        run_config = read_run_config_identity(guard_store)

        if run_config.run_id != run_id:
            raise RuntimeError("Cancellation cleanup run ID does not match sentinel")

        return cleanup_confirmed_cancelled_job(
            guard_store=guard_store,
            run_config=run_config,
            job_id=job_id,
            claim_token=claim_token,
            audio_root=AUDIO_DIR,
            commit_audio=audio_volume.commit,
        )

    return create_app(
        job_store=job_store,
        enqueue_generation=enqueue_generation,
        audio_root=AUDIO_DIR,
        reload_audio=audio_volume.reload,
        password=os.environ.get("PORTAL_PASSWORD", ""),
        model_name="ACE-Step 1.5",
        guard_store=guard_store,
        run_config_reader=lambda: read_run_config_identity(guard_store),
        cancel_generation=cancel_generation,
        generation_termination_confirmed=generation_termination_confirmed,
        cleanup_cancelled_generation=cleanup_cancelled_generation,
        generation_call_state=call_result_state,
        require_guard=True,
        generation_enabled=False,
        public_preview=True,
        fixed_generation_duration_seconds=VALIDATION_DURATION_SECONDS,
    )
