"""Guarded worker orchestration for SONORA.

This module has no Modal dependency. It ensures that:
- cancellation is checked before and after synchronous generation;
- finalization ownership is acquired before library metadata is written;
- a worker cannot publish after cancellation has won;
- failed work is cleaned only after its generation callback has returned;
- completed terminal outcomes are never rolled back.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable

from sonora.guard import (
    RunConfig,
    claim_terminal,
    cleanup_job_files,
    read_terminal_state,
    record_terminal_result,
)
from sonora.library import resolve_audio_file, write_track_record


class GenerationStopped(RuntimeError):
    """Raised by a generation callback when the guard shows a stop request."""

    def __init__(self, state: str) -> None:
        super().__init__(f"Generation stopped by guard state: {state}")
        self.state = state


def assert_job_active(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
) -> None:
    """Raise when cancellation or another terminal action has won."""
    state = read_terminal_state(guard_store, run_config, job_id)
    if state is not None:
        raise GenerationStopped(state["state"])


def _job_audio_file(
    audio_root: Path,
    job_id: str,
    generated_file: str | Path,
) -> tuple[Path, str]:
    """Require generated audio to remain inside this job's own directory."""
    canonical_id = str(uuid.UUID(str(job_id)))
    root = audio_root.resolve()
    job_dir = (root / "jobs" / canonical_id).resolve()

    candidate = Path(generated_file)
    if not candidate.is_absolute():
        candidate = root / candidate

    resolved = resolve_audio_file(
        root,
        candidate.relative_to(root).as_posix()
        if candidate.is_relative_to(root)
        else str(candidate),
    )

    try:
        resolved.relative_to(job_dir)
    except ValueError as exc:
        raise ValueError("Generated audio must be inside this job's directory") from exc

    if not resolved.is_file():
        raise FileNotFoundError("Generated audio file is missing")

    return resolved, resolved.relative_to(root).as_posix()


def _guard_state(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
) -> str | None:
    state = read_terminal_state(guard_store, run_config, job_id)
    return state["state"] if state is not None else None


def _record_generation_failure(
    *,
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    audio_root: Path,
    commit_audio: Callable[[], Any],
    clock: Callable[[], float],
) -> bool:
    """Record and clean a synchronous generation failure if failure wins.

    This function is called only after the synchronous generation callback
    has raised. If cancellation or another terminal claim won, it does not
    clean files while the other actor may still be active.
    """
    token = claim_terminal(
        guard_store,
        run_config,
        job_id,
        "failed",
        clock=clock,
    )

    if token is None:
        return False

    record_terminal_result(
        guard_store,
        run_config,
        job_id,
        owner_claim="failed",
        claim_token=token,
        result="failed",
        clock=clock,
    )

    cleanup_job_files(
        audio_root,
        job_id,
        guard_store=guard_store,
        run_config=run_config,
        claim_token=token,
        clock=clock,
    )
    commit_audio()
    return True


def run_guarded_generation(
    *,
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    audio_root: str | Path,
    prompt: str,
    lyrics: str,
    duration_seconds: int,
    generate_audio: Callable[[], str | Path],
    commit_audio: Callable[[], Any],
    engine: str,
    model_revision: str,
    created_at: float,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run one synchronous generation and finalize it under guard ownership.

    Returns a small result mapping containing status and, on success, file
    metadata. A cancellation claim that wins before finalization prevents
    library publication. Partial files are intentionally left for confirmed
    cancellation cleanup by the HTTP/Modal reconciliation path.
    """
    initial_state = _guard_state(guard_store, run_config, job_id)
    if initial_state is not None:
        return {
            "status": initial_state,
            "published": False,
        }

    try:
        generated_file = generate_audio()
        audio_path, relative_audio_path = _job_audio_file(
            Path(audio_root),
            job_id,
            generated_file,
        )
    except GenerationStopped as stopped:
        return {
            "status": stopped.state,
            "published": False,
        }
    except Exception as exc:
        cleaned = _record_generation_failure(
            guard_store=guard_store,
            run_config=run_config,
            job_id=job_id,
            audio_root=Path(audio_root),
            commit_audio=commit_audio,
            clock=clock,
        )

        return {
            "status": "failed" if cleaned else (_guard_state(
                guard_store,
                run_config,
                job_id,
            ) or "failed"),
            "published": False,
            "error": str(exc),
        }

    # Generation may have completed while cancellation was being requested.
    # Re-check before acquiring finalization ownership.
    state_after_generation = _guard_state(guard_store, run_config, job_id)
    if state_after_generation is not None:
        return {
            "status": state_after_generation,
            "published": False,
            "partial_audio_retained": True,
        }

    # The atomic finalizing claim must happen before writing library metadata.
    finalizing_token = claim_terminal(
        guard_store,
        run_config,
        job_id,
        "finalizing",
        clock=clock,
    )
    if finalizing_token is None:
        return {
            "status": _guard_state(guard_store, run_config, job_id) or "blocked",
            "published": False,
        }

    title = " ".join(prompt.split())
    if len(title) > 72:
        title = title[:69].rstrip() + "..."
    title = title or "Untitled track"

    record = {
        "job_id": str(uuid.UUID(str(job_id))),
        "title": title,
        "status": "complete",
        "prompt": prompt,
        "lyrics": lyrics,
        "filename": audio_path.name,
        "file": relative_audio_path,
        "duration_seconds": duration_seconds,
        "created_at": created_at,
        "completed_at": clock(),
        "engine": engine,
        "model_revision": model_revision,
    }

    try:
        write_track_record(audio_root, record)
        commit_audio()

        # The durable outcome is published only after the Volume commit.
        record_terminal_result(
            guard_store,
            run_config,
            job_id,
            owner_claim="finalizing",
            claim_token=finalizing_token,
            result="complete",
            clock=clock,
        )
    except Exception as exc:
        # This token can roll back only this worker's finalization. The helper
        # atomically records "failed"; it refuses if "complete" already won.
        try:
            cleanup_job_files(
                audio_root,
                job_id,
                guard_store=guard_store,
                run_config=run_config,
                claim_token=finalizing_token,
                clock=clock,
                finalization_rollback=True,
            )
            commit_audio()
        except Exception as cleanup_exc:
            if hasattr(exc, "add_note"):
                exc.add_note(
                    "Finalization rollback/cleanup also failed: "
                    f"{cleanup_exc!r}"
                )
            raise exc from cleanup_exc

        return {
            "status": "failed",
            "published": False,
            "error": str(exc),
        }

    return {
        "status": "complete",
        "published": True,
        "job_id": record["job_id"],
        "title": title,
        "file": relative_audio_path,
        "filename": audio_path.name,
        "duration_seconds": duration_seconds,
        "completed_at": record["completed_at"],
    }


def cleanup_confirmed_cancelled_job(
    *,
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    claim_token: str,
    audio_root: str | Path,
    commit_audio: Callable[[], Any],
    clock: Callable[[], float] = time.time,
) -> tuple[str, ...]:
    """Clean partial files only after cancellation is terminally confirmed.

    The caller must first establish that the Modal FunctionCall is no longer
    executing and write the guard outcome `cancelled` using this claim token.
    """
    state = read_terminal_state(guard_store, run_config, job_id)
    if state is None or state["state"] != "cancelled":
        raise RuntimeError(
            "Cancellation cleanup requires a confirmed cancelled guard state"
        )

    removed = cleanup_job_files(
        audio_root,
        job_id,
        guard_store=guard_store,
        run_config=run_config,
        claim_token=claim_token,
        clock=clock,
    )

    if removed:
        commit_audio()

    return removed
