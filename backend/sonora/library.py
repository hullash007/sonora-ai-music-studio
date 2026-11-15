"""Filesystem-backed music-library metadata helpers.

This module intentionally uses only the Python standard library so it can be
unit-tested locally without importing Modal, starting a container, or calling
any paid service.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


def _validate_job_id(job_id: str) -> str:
    """Return a canonical UUID string or raise ValueError."""
    try:
        return str(uuid.UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid track ID") from exc


def _library_dir(audio_root: str | os.PathLike[str]) -> Path:
    root = Path(audio_root).resolve()
    return root / "library"


def write_track_record(audio_root: str | os.PathLike[str], record: dict[str, Any]) -> Path:
    """Atomically persist a completed track record as library/<uuid>.json."""
    job_id = _validate_job_id(record.get("job_id", ""))
    if record.get("status") != "complete":
        raise ValueError("Only completed tracks can be added to the library")

    relative_audio_path = record.get("file")
    if not isinstance(relative_audio_path, str) or not relative_audio_path.strip():
        raise ValueError("Track record must contain a relative audio file path")
    resolve_audio_file(audio_root, relative_audio_path)

    library_dir = _library_dir(audio_root)
    library_dir.mkdir(parents=True, exist_ok=True)
    destination = library_dir / f"{job_id}.json"
    temporary = library_dir / f".{job_id}.{uuid.uuid4().hex}.tmp"

    normalized = dict(record)
    normalized["job_id"] = job_id
    normalized["file"] = Path(relative_audio_path).as_posix()

    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(normalized, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    return destination


def read_track_record(audio_root: str | os.PathLike[str], job_id: str) -> dict[str, Any] | None:
    """Read one completed track record; return None for missing/corrupt data."""
    try:
        canonical_id = _validate_job_id(job_id)
    except ValueError:
        return None

    record_path = _library_dir(audio_root) / f"{canonical_id}.json"
    try:
        with record_path.open("r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(record, dict):
        return None
    if record.get("job_id") != canonical_id or record.get("status") != "complete":
        return None
    if not isinstance(record.get("file"), str):
        return None
    return record


def list_track_records(
    audio_root: str | os.PathLike[str], *, limit: int = 200
) -> list[dict[str, Any]]:
    """List valid records newest-first, with a bounded result count."""
    if not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")

    library_dir = _library_dir(audio_root)
    if not library_dir.is_dir():
        return []

    records: list[dict[str, Any]] = []
    for record_path in library_dir.glob("*.json"):
        job_id = record_path.stem
        record = read_track_record(audio_root, job_id)
        if record is not None:
            records.append(record)

    def timestamp(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    records.sort(
        key=lambda item: (timestamp(item.get("created_at")), timestamp(item.get("completed_at"))),
        reverse=True,
    )
    return records[:limit]


def resolve_audio_file(
    audio_root: str | os.PathLike[str], relative_path: str, *, require_exists: bool = True
) -> Path:
    """Resolve a relative audio path and prevent escaping the audio root."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Audio path is required")

    root = Path(audio_root).resolve()
    candidate_input = Path(relative_path)
    if candidate_input.is_absolute():
        raise ValueError("Audio path must be relative")

    candidate = (root / candidate_input).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid audio path") from exc

    if require_exists and not candidate.is_file():
        raise FileNotFoundError("Audio file is unavailable")
    return candidate
