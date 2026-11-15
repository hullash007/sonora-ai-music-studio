"""SONORA isolated staging app: CPU portal plus on-demand ACE-Step GPU worker.

Do not deploy this file without explicit approval. All Modal resource names are
staging-specific; this app does not mount or read production storage or secrets.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import modal

APP_NAME = "sonora-staging"
PROJECT_DIR = "/opt/ACE-Step-1.5"
CHECKPOINT_DIR = f"{PROJECT_DIR}/checkpoints"
AUDIO_DIR = "/mnt/sonora-staging-audio"
BACKEND_DIR = "/opt/sonora/backend"
ACE_STEP_COMMIT = "ca1e85fe9430179831e6bc6be790c332190a3866"

app = modal.App(APP_NAME)

# Isolated resources: these names are intentionally distinct from production.
checkpoints_volume = modal.Volume.from_name(
    "sonora-staging-checkpoints", create_if_missing=True
)
audio_volume = modal.Volume.from_name("sonora-staging-audio", create_if_missing=True)
job_store = modal.Dict.from_name("sonora-staging-jobs", create_if_missing=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BACKEND = str(REPO_ROOT / "backend")

portal_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi")
    .add_local_dir(LOCAL_BACKEND, remote_path=BACKEND_DIR)
)

music_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg", "libsndfile1", "libgomp1")
    .pip_install("uv")
    .run_commands(
        f"git clone https://github.com/ACE-Step/ACE-Step-1.5.git {PROJECT_DIR} "
        f"&& cd {PROJECT_DIR} && git checkout {ACE_STEP_COMMIT}"
    )
    .run_commands(f"cd {PROJECT_DIR} && uv sync --no-dev --python 3.12")
    .add_local_dir(LOCAL_BACKEND, remote_path=BACKEND_DIR)
)


@app.function(
    image=music_image,
    gpu=["A10", "L4"],
    cpu=8,
    memory=32768,
    timeout=1800,
    startup_timeout=1800,
    min_containers=0,
    max_containers=1,
    scaledown_window=30,
    volumes={
        f"{PROJECT_DIR}/checkpoints": checkpoints_volume,
        AUDIO_DIR: audio_volume,
    },
)
def generate_track(job_id: str, prompt: str, lyrics: str, duration: int) -> None:
    """Generate one track and persist audio plus a durable library record."""
    sys.path.insert(0, BACKEND_DIR)
    from sonora.library import write_track_record

    def update_job(**updates: Any) -> None:
        current = job_store.get(job_id, {"job_id": job_id})
        current.update(updates)
        job_store[job_id] = current

    try:
        update_job(status="running", stage="Starting staging GPU worker", error=None)
        sys.path.insert(0, PROJECT_DIR)
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

        update_job(stage="Loading ACE-Step music model")
        from acestep.handler import AceStepHandler
        from acestep.llm_inference import LLMHandler
        from acestep.inference import GenerationConfig, GenerationParams, generate_music
        from acestep.api.model_download import ensure_model_downloaded

        ensure_model_downloaded("acestep-5Hz-lm-1.7B", CHECKPOINT_DIR)
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
            raise RuntimeError(f"ACE-Step DiT initialization failed: {dit_message}")

        update_job(stage="Loading ACE-Step language model")
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
            raise RuntimeError(f"ACE-Step language-model initialization failed: {llm_message}")

        update_job(stage="Generating music")
        job_dir = os.path.join(AUDIO_DIR, "jobs", job_id)
        os.makedirs(job_dir, exist_ok=True)
        params = GenerationParams(
            task_type="text2music",
            caption=prompt,
            lyrics=lyrics if lyrics.strip() else "[Instrumental]",
            duration=float(duration),
            vocal_language="unknown",
            thinking=True,
            use_cot_metas=True,
            use_cot_caption=True,
            use_cot_language=True,
            inference_steps=8,
            shift=3.0,
        )
        config = GenerationConfig(
            batch_size=1,
            allow_lm_batch=False,
            use_random_seed=True,
            audio_format="mp3",
            mp3_bitrate="128k",
            mp3_sample_rate=48000,
        )
        result = generate_music(dit_handler, llm_handler, params, config, save_dir=job_dir)
        if not result.success:
            raise RuntimeError(result.error or result.status_message or "Music generation failed")
        if not result.audios:
            raise RuntimeError("ACE-Step completed without returning an audio file")

        audio_path = result.audios[0].get("path")
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError(f"Generated audio file was not found: {audio_path}")
        audio_abs = os.path.abspath(audio_path)
        job_root = os.path.abspath(job_dir)
        if os.path.commonpath([audio_abs, job_root]) != job_root:
            raise RuntimeError("Generated audio path is outside the job output directory")

        relative_audio_path = os.path.relpath(audio_abs, AUDIO_DIR).replace("\\", "/")
        initial = job_store.get(job_id, {})
        created_at = float(initial.get("created_at", time.time()))
        completed_at = time.time()
        title = " ".join(prompt.split())
        if len(title) > 72:
            title = title[:69].rstrip() + "..."

        library_record = {
            "job_id": job_id,
            "title": title or "Untitled track",
            "status": "complete",
            "prompt": prompt,
            "lyrics": lyrics,
            "filename": os.path.basename(audio_abs),
            "file": relative_audio_path,
            "duration_seconds": duration,
            "created_at": created_at,
            "completed_at": completed_at,
            "engine": "ACE-Step 1.5",
            "model_revision": ACE_STEP_COMMIT,
        }
        write_track_record(AUDIO_DIR, library_record)
        audio_volume.commit()
        update_job(
            status="complete",
            stage="Music generation complete",
            file=relative_audio_path,
            filename=os.path.basename(audio_abs),
            title=title or "Untitled track",
            duration_seconds=duration,
            completed_at=completed_at,
            error=None,
        )
    except Exception as exc:
        update_job(
            status="failed",
            stage="Generation failed",
            error=str(exc),
            traceback=traceback.format_exc()[-12000:],
        )
        raise


@app.function(
    image=portal_image,
    cpu=1,
    memory=1024,
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    volumes={AUDIO_DIR: audio_volume},
    secrets=[modal.Secret.from_name("sonora-staging-portal")],
)
@modal.asgi_app()
def music_portal():
    """Expose the dependency-injected SONORA HTTP app on isolated staging."""

    sys.path.insert(0, BACKEND_DIR)

    from sonora.web import create_app

    def enqueue_generation(
        job_id: str,
        prompt: str,
        lyrics: str,
        duration: int,
    ) -> None:
        """Schedule the existing staging-only GPU generation function."""
        generate_track.spawn(job_id, prompt, lyrics, duration)

    return create_app(
        job_store=job_store,
        enqueue_generation=enqueue_generation,
        audio_root=AUDIO_DIR,
        reload_audio=audio_volume.reload,
        password=os.environ.get("PORTAL_PASSWORD", ""),
        model_name="ACE-Step 1.5",
    )
