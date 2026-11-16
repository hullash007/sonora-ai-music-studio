from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

# Ensure `sonora` is importable when this script is launched from the repo root
# or directly by its script path.
sys.path.insert(0, str(BACKEND_DIR))

from sonora.library import write_track_record  # noqa: E402
from sonora.web import create_app  # noqa: E402


DEFAULT_DATA_DIR = Path(tempfile.gettempdir()) / "sonora-local-studio"
DEFAULT_PASSWORD = "local-sonora-demo-password"

SAMPLE_RATE = 22_050
DEMO_AUDIO_SECONDS = 2
DEMO_FREQUENCY_HZ = 440.0


def write_wav_fixture(destination: Path) -> None:
    """Write a small, deterministic two-second WAV tone using the stdlib."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    frame_count = SAMPLE_RATE * DEMO_AUDIO_SECONDS
    fade_frames = max(1, int(SAMPLE_RATE * 0.05))
    samples = bytearray()

    for index in range(frame_count):
        fade_in = min(1.0, index / fade_frames)
        fade_out = min(1.0, (frame_count - 1 - index) / fade_frames)
        envelope = max(0.0, min(fade_in, fade_out))

        sample_value = int(
            0.18
            * 32767
            * envelope
            * math.sin(
                2.0
                * math.pi
                * DEMO_FREQUENCY_HZ
                * index
                / SAMPLE_RATE
            )
        )
        samples.extend(struct.pack("<h", sample_value))

    with wave.open(str(destination), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(samples)


def create_fake_queue(
    *,
    audio_root: Path,
    job_store: dict[str, dict[str, Any]],
):
    """Return a synchronous fake generation adapter for local browser testing."""

    def enqueue_generation(
        job_id: str,
        prompt: str,
        lyrics: str,
        duration: int,
    ) -> None:
        existing_job = job_store.get(job_id, {})
        created_at = float(existing_job.get("created_at", time.time()))

        relative_audio_path = f"jobs/{job_id}/sonora-local-demo.wav"
        absolute_audio_path = audio_root / relative_audio_path

        try:
            write_wav_fixture(absolute_audio_path)

            completed_at = time.time()
            title = " ".join(prompt.split())
            if len(title) > 72:
                title = title[:69].rstrip() + "..."

            record = {
                "job_id": job_id,
                "title": title or "Local WAV demo",
                "status": "complete",
                "prompt": prompt,
                "lyrics": lyrics,
                "filename": absolute_audio_path.name,
                "file": relative_audio_path,
                # This is the fixture's actual duration, not the requested
                # ACE-Step duration submitted in the form.
                "duration_seconds": DEMO_AUDIO_SECONDS,
                "created_at": created_at,
                "completed_at": completed_at,
                "engine": "Local fake adapter — WAV fixture",
                "model_revision": "no-model-used",
            }

            write_track_record(audio_root, record)

            job_store[job_id] = {
                **existing_job,
                "job_id": job_id,
                "status": "complete",
                "stage": "Local WAV demo fixture complete (no model or GPU used)",
                "file": relative_audio_path,
                "filename": absolute_audio_path.name,
                "title": record["title"],
                "duration_seconds": DEMO_AUDIO_SECONDS,
                "completed_at": completed_at,
                "error": None,
            }

        except Exception as exc:
            job_store[job_id] = {
                **existing_job,
                "job_id": job_id,
                "status": "failed",
                "stage": "Local WAV fixture creation failed",
                "error": str(exc),
            }
            raise

    return enqueue_generation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the SONORA local browser studio with fake generation "
            "and a locally generated WAV fixture."
        )
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Defaults to loopback only: 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Local HTTP port. Defaults to 8000.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=(
            "Persistent local audio/library directory. "
            "Defaults to the system temporary directory's sonora-local-studio folder."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Uvicorn is required to run the local browser studio. "
            'Install it with: python -m pip install "uvicorn>=0.30,<1"'
        ) from exc

    if not 1 <= args.port <= 65535:
        raise SystemExit("Port must be between 1 and 65535.")

    audio_root = args.data_dir.expanduser().resolve()
    audio_root.mkdir(parents=True, exist_ok=True)

    # This mapping exists only in this local Python process.
    job_store: dict[str, dict[str, Any]] = {}

    enqueue_generation = create_fake_queue(
        audio_root=audio_root,
        job_store=job_store,
    )

    password = os.environ.get("SONORA_LOCAL_PASSWORD", DEFAULT_PASSWORD)

    app = create_app(generation_enabled=True,
        job_store=job_store,
        enqueue_generation=enqueue_generation,
        audio_root=audio_root,
        reload_audio=lambda: None,
        password=password,
        model_name="Local WAV fixture — no model or GPU",
    )

    print()
    print("SONORA local studio")
    print("-------------------")
    print("Mode: fake generation adapter; no ACE-Step model or GPU")
    print(f"Studio URL: http://{args.host}:{args.port}")
    print("Password: use SONORA_LOCAL_PASSWORD if set, otherwise the documented demo password.")
    print(f"Persistent library directory: {audio_root}")
    print()
    print("Submitted tracks are two-second WAV tone fixtures, not generated music.")
    print("Existing library records in the directory are retained between runs.")
    print("Press Ctrl+C to stop the local server.")
    print()

    # No Modal import, deployment, GPU call or outbound service request occurs.
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
