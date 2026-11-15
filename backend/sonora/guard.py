"""Fail-closed, single-attempt safeguards for SONORA Phase 1.

No Modal imports. The run-config sentinel must be provisioned separately.
This module never creates, edits, rearms or deletes that sentinel.

Required store API:
    get(key)
    put(key, value, skip_if_exists=True) -> bool

Conditional insertion must be atomic in the real store.
The admission key must never be deleted or overwritten while its one-hour
sentinel is valid. This guard cannot detect manual deletion of its own history.
Suspected alteration invalidates the run; it is not a tamper-proof ledger.
"""

from __future__ import annotations

import json
import math
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal


RUN_CONFIG_KEY = "sonora:phase1:run-config"
RUN_CONFIG_KIND = "sonora-phase1-run-config"
RUN_CONFIG_SCHEMA_VERSION = 1
MAX_RUN_WINDOW_SECONDS = 3600
MINIMUM_RUN_REMAINING_SECONDS = 2100

TerminalClaim = Literal["cancelled", "finalizing", "failed"]
TerminalResult = Literal[
    "cancelled",
    "cancellation_failed",
    "complete",
    "failed",
]

_ALLOWED_RESULTS: dict[str, set[str]] = {
    "cancelled": {"cancelled", "cancellation_failed"},
    "finalizing": {"complete", "failed"},
    "failed": {"failed"},
}

_PROGRESS_STATES = {
    "cancelled": "cancelling",
    "finalizing": "finalizing",
    "failed": "failed",
}


class GuardError(RuntimeError):
    """Base class for fail-closed guard errors."""


class GuardUnavailable(GuardError):
    """The guard store is unavailable or contains malformed records."""


class InvalidRunConfig(GuardError):
    """The immutable run-config sentinel is missing or invalid."""


class RunExpired(GuardError):
    """The admission window has expired or has insufficient time remaining."""


class AttemptAlreadyClaimed(GuardError):
    """The single generation attempt has already been consumed."""


class InvalidTerminalTransition(GuardError):
    """A terminal claim or result transition is invalid."""


class LibraryRecordProtected(GuardError):
    """Cleanup would remove or orphan a library record."""


class UnsafeCleanupPath(GuardError):
    """Cleanup would follow a symlink or escape the job's own directory."""


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    issued_at: float
    expires_at: float
    max_attempts: int = 1


def _now(clock: Callable[[], float]) -> float:
    try:
        value = clock()
        if isinstance(value, bool):
            raise ValueError("Boolean clock value")
        result = float(value)
    except Exception as exc:
        raise GuardUnavailable("Guard clock is unavailable") from exc

    if not math.isfinite(result):
        raise GuardUnavailable("Guard clock returned a non-finite value")
    return result


def _get(store: Any, key: str) -> Any:
    if store is None or not callable(getattr(store, "get", None)):
        raise GuardUnavailable("Guard store is unavailable")

    try:
        return store.get(key)
    except Exception as exc:
        raise GuardUnavailable("Could not read guard store") from exc


def _put(
    store: Any,
    key: str,
    value: dict[str, Any],
    *,
    skip_if_exists: bool,
) -> bool:
    if store is None or not callable(getattr(store, "put", None)):
        raise GuardUnavailable("Guard store is unavailable")

    try:
        result = store.put(
            key,
            value,
            skip_if_exists=skip_if_exists,
        )
    except Exception as exc:
        raise GuardUnavailable("Could not write guard store") from exc

    if type(result) is not bool:
        raise GuardUnavailable("Guard store did not return a boolean result")
    return result


def _canonical_job_id(job_id: str) -> str:
    try:
        return str(uuid.UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid job ID") from exc


def _canonical_run_id(value: Any) -> str:
    if not isinstance(value, str):
        raise InvalidRunConfig("Run-config run_id must be a UUID")

    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidRunConfig("Run-config run_id must be a UUID") from exc

    if canonical != value:
        raise InvalidRunConfig("Run-config run_id must be canonical")
    return canonical


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRunConfig(f"{name} must be numeric")

    result = float(value)
    if not math.isfinite(result):
        raise InvalidRunConfig(f"{name} must be finite")
    return result


def _stored_number(record: dict[str, Any], name: str) -> float:
    try:
        return _finite_number(record.get(name), name)
    except InvalidRunConfig as exc:
        raise GuardUnavailable(f"Persisted {name} is malformed") from exc


def _read_run_config_identity(store: Any) -> RunConfig:
    """Validate sentinel identity and structure without enforcing expiry."""
    sentinel = _get(store, RUN_CONFIG_KEY)

    if not isinstance(sentinel, dict):
        raise InvalidRunConfig("Run-config sentinel is missing or malformed")
    if sentinel.get("kind") != RUN_CONFIG_KIND:
        raise InvalidRunConfig("Invalid run-config sentinel kind")
    if type(sentinel.get("schema_version")) is not int:
        raise InvalidRunConfig("Invalid run-config schema version")
    if sentinel["schema_version"] != RUN_CONFIG_SCHEMA_VERSION:
        raise InvalidRunConfig("Unsupported run-config schema version")

    run_id = _canonical_run_id(sentinel.get("run_id"))
    issued_at = _finite_number(sentinel.get("issued_at"), "issued_at")
    expires_at = _finite_number(sentinel.get("expires_at"), "expires_at")
    max_attempts = sentinel.get("max_attempts")

    if type(max_attempts) is not int or max_attempts != 1:
        raise InvalidRunConfig("Run-config must permit exactly one attempt")

    window = expires_at - issued_at
    if window <= 0 or window > MAX_RUN_WINDOW_SECONDS:
        raise InvalidRunConfig("Run-config expiry window must be at most one hour")

    return RunConfig(
        run_id=run_id,
        issued_at=issued_at,
        expires_at=expires_at,
        max_attempts=max_attempts,
    )


def validate_run_config(
    guard_store: Any,
    *,
    clock: Callable[[], float] = time.time,
    minimum_remaining_seconds: float = 0,
) -> RunConfig:
    """Validate sentinel and require an active window. Use for admission only."""
    if (
        isinstance(minimum_remaining_seconds, bool)
        or not isinstance(minimum_remaining_seconds, (int, float))
        or not math.isfinite(float(minimum_remaining_seconds))
        or minimum_remaining_seconds < 0
    ):
        raise ValueError("minimum_remaining_seconds must be non-negative")

    config = _read_run_config_identity(guard_store)
    now = _now(clock)
    if config.issued_at > now:
        raise InvalidRunConfig("Run-config issue time is in the future")
    remaining = config.expires_at - now

    if remaining <= 0:
        raise RunExpired("Run-config approval window has expired")
    if remaining < float(minimum_remaining_seconds):
        raise RunExpired("Insufficient approval time remains for this attempt")
    return config


def _require_same_run(guard_store: Any, run_config: RunConfig) -> RunConfig:
    """Require the sentinel to exist and match, but do not enforce expiry."""
    if not isinstance(run_config, RunConfig):
        raise InvalidRunConfig("A validated RunConfig is required")

    current = _read_run_config_identity(guard_store)
    if current != run_config:
        raise InvalidRunConfig("Run-config changed since job admission")
    return current


def admission_key(run_id: str) -> str:
    return f"sonora:run:{_canonical_run_id(run_id)}:admission"


def terminal_claim_key(run_id: str, job_id: str) -> str:
    return (
        f"sonora:run:{_canonical_run_id(run_id)}:"
        f"job:{_canonical_job_id(job_id)}:terminal-claim"
    )


def terminal_result_key(run_id: str, job_id: str) -> str:
    return (
        f"sonora:run:{_canonical_run_id(run_id)}:"
        f"job:{_canonical_job_id(job_id)}:terminal-result"
    )


def _require_admitted_job(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
) -> dict[str, Any]:
    canonical_job_id = _canonical_job_id(job_id)
    admission = _get(guard_store, admission_key(run_config.run_id))

    if not isinstance(admission, dict):
        raise GuardUnavailable("The admitted-attempt record is missing or malformed")

    if (
        admission.get("run_id") != run_config.run_id
        or admission.get("job_id") != canonical_job_id
        or type(admission.get("max_attempts")) is not int
        or admission.get("max_attempts") != 1
    ):
        raise GuardUnavailable("The admitted-attempt record does not match this job")

    _stored_number(admission, "claimed_at")
    return admission


def claim_one_attempt(
    guard_store: Any,
    job_id: str,
    *,
    clock: Callable[[], float] = time.time,
    minimum_remaining_seconds: float = MINIMUM_RUN_REMAINING_SECONDS,
) -> RunConfig:
    """Atomically consume the one permitted generation attempt."""
    canonical_job_id = _canonical_job_id(job_id)

    # Only new admission enforces the absolute expiry and remaining-time budget.
    config = validate_run_config(
        guard_store,
        clock=clock,
        minimum_remaining_seconds=minimum_remaining_seconds,
    )

    admission = {
        "run_id": config.run_id,
        "job_id": canonical_job_id,
        "claimed_at": _now(clock),
        "max_attempts": 1,
    }

    inserted = _put(
        guard_store,
        admission_key(config.run_id),
        admission,
        skip_if_exists=True,
    )
    if not inserted:
        raise AttemptAlreadyClaimed("The one permitted attempt is already consumed")

    # If the sentinel disappeared during admission, the attempt stays consumed.
    _require_same_run(guard_store, config)
    return config


def _read_claim(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
) -> dict[str, Any] | None:
    canonical_job_id = _canonical_job_id(job_id)
    record = _get(
        guard_store,
        terminal_claim_key(run_config.run_id, canonical_job_id),
    )

    if record is None:
        return None
    if not isinstance(record, dict):
        raise GuardUnavailable("Terminal claim is malformed")

    owner = record.get("claim")
    if (
        record.get("run_id") != run_config.run_id
        or record.get("job_id") != canonical_job_id
        or not isinstance(owner, str)
        or owner not in _ALLOWED_RESULTS
    ):
        raise GuardUnavailable("Terminal claim identity or owner is invalid")

    token = record.get("claim_token")
    if not isinstance(token, str) or len(token) != 32:
        raise GuardUnavailable("Terminal claim token is malformed")

    try:
        if uuid.UUID(hex=token).hex != token:
            raise ValueError("Non-canonical token")
    except (ValueError, AttributeError) as exc:
        raise GuardUnavailable("Terminal claim token is malformed") from exc

    _stored_number(record, "claimed_at")
    return record


def _read_outcome(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    claim: dict[str, Any],
) -> dict[str, Any] | None:
    canonical_job_id = _canonical_job_id(job_id)
    record = _get(
        guard_store,
        terminal_result_key(run_config.run_id, canonical_job_id),
    )

    if record is None:
        return None
    if not isinstance(record, dict):
        raise GuardUnavailable("Terminal result is malformed")

    owner = claim["claim"]
    result = record.get("result")

    if (
        record.get("run_id") != run_config.run_id
        or record.get("job_id") != canonical_job_id
        or record.get("owner_claim") != owner
        or record.get("claim_token") != claim["claim_token"]
        or not isinstance(result, str)
        or result not in _ALLOWED_RESULTS[owner]
    ):
        raise GuardUnavailable("Terminal result identity or owner is invalid")

    _stored_number(record, "updated_at")
    return record


def claim_terminal(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    claim: TerminalClaim,
    *,
    clock: Callable[[], float] = time.time,
) -> str | None:
    """Atomically claim terminal ownership and return its unique token.

    Returns None if another terminal actor already won. Expiry is deliberately
    not enforced: this operates on an already admitted job.
    """
    if not isinstance(claim, str) or claim not in _ALLOWED_RESULTS:
        raise ValueError("Unsupported terminal claim")

    canonical_job_id = _canonical_job_id(job_id)
    _require_same_run(guard_store, run_config)
    _require_admitted_job(guard_store, run_config, canonical_job_id)

    token = uuid.uuid4().hex
    record = {
        "run_id": run_config.run_id,
        "job_id": canonical_job_id,
        "claim": claim,
        "claim_token": token,
        "claimed_at": _now(clock),
    }

    inserted = _put(
        guard_store,
        terminal_claim_key(run_config.run_id, canonical_job_id),
        record,
        skip_if_exists=True,
    )

    if not inserted:
        return None

    _require_same_run(guard_store, run_config)
    return token


def record_terminal_result(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    *,
    owner_claim: TerminalClaim,
    claim_token: str,
    result: TerminalResult,
    clock: Callable[[], float] = time.time,
) -> None:
    """Atomically write a final result once, using the winning claim token."""
    if not isinstance(owner_claim, str) or owner_claim not in _ALLOWED_RESULTS:
        raise InvalidTerminalTransition("Unsupported terminal owner")
    if not isinstance(result, str) or result not in _ALLOWED_RESULTS[owner_claim]:
        raise InvalidTerminalTransition(
            f"Result {result!r} is invalid for owner {owner_claim!r}"
        )

    canonical_job_id = _canonical_job_id(job_id)
    _require_same_run(guard_store, run_config)
    _require_admitted_job(guard_store, run_config, canonical_job_id)

    claim = _read_claim(guard_store, run_config, canonical_job_id)
    if claim is None:
        raise InvalidTerminalTransition("Terminal claim is missing")

    if claim["claim"] != owner_claim or claim["claim_token"] != claim_token:
        raise InvalidTerminalTransition("Terminal claim token or owner does not match")

    outcome = {
        "run_id": run_config.run_id,
        "job_id": canonical_job_id,
        "owner_claim": owner_claim,
        "claim_token": claim_token,
        "result": result,
        "updated_at": _now(clock),
    }

    inserted = _put(
        guard_store,
        terminal_result_key(run_config.run_id, canonical_job_id),
        outcome,
        skip_if_exists=True,
    )
    if inserted:
        return

    # Same-token/same-result retry is idempotent. Any conflicting outcome loses.
    existing_claim = _read_claim(guard_store, run_config, canonical_job_id)
    existing = _read_outcome(
        guard_store,
        run_config,
        canonical_job_id,
        existing_claim,
    )

    if (
        existing is not None
        and existing["owner_claim"] == owner_claim
        and existing["claim_token"] == claim_token
        and existing["result"] == result
    ):
        return

    raise InvalidTerminalTransition("A different final outcome already won")


def read_terminal_state(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
) -> dict[str, Any] | None:
    """Read and validate terminal identity; expiry does not block admitted jobs."""
    _require_same_run(guard_store, run_config)
    _require_admitted_job(guard_store, run_config, job_id)

    claim = _read_claim(guard_store, run_config, job_id)
    if claim is None:
        return None

    outcome = _read_outcome(guard_store, run_config, job_id, claim)
    result = outcome["result"] if outcome is not None else None

    return {
        "run_id": run_config.run_id,
        "job_id": _canonical_job_id(job_id),
        "claim": claim["claim"],
        "result": result,
        "state": result if result is not None else _PROGRESS_STATES[claim["claim"]],
    }


def _safe_job_directory(root: Path, job_id: str) -> Path | None:
    jobs_parent = root / "jobs"

    if jobs_parent.is_symlink():
        raise UnsafeCleanupPath("Refusing symlinked jobs directory")
    if not jobs_parent.exists():
        return None

    resolved_parent = jobs_parent.resolve()
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise UnsafeCleanupPath("Jobs directory escapes audio root") from exc

    candidate = jobs_parent / job_id
    if candidate.is_symlink():
        raise UnsafeCleanupPath("Refusing symlinked job directory")
    if not candidate.exists():
        return None

    resolved_child = candidate.resolve()
    if resolved_child.parent != resolved_parent or not resolved_child.is_dir():
        raise UnsafeCleanupPath("Job directory escapes its own parent")
    return resolved_child


def _library_record_path(root: Path, job_id: str) -> Path | None:
    library_parent = root / "library"

    if library_parent.is_symlink():
        raise UnsafeCleanupPath("Refusing symlinked library directory")
    if not library_parent.exists():
        return None

    resolved_parent = library_parent.resolve()
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise UnsafeCleanupPath("Library directory escapes audio root") from exc

    candidate = library_parent / f"{job_id}.json"
    if candidate.is_symlink():
        raise UnsafeCleanupPath("Refusing symlinked library metadata")
    if not candidate.exists():
        return None

    resolved_candidate = candidate.resolve()
    if resolved_candidate.parent != resolved_parent or not resolved_candidate.is_file():
        raise UnsafeCleanupPath("Library metadata path is unsafe")
    return resolved_candidate


def _validate_rollback_record(
    metadata_path: Path,
    root: Path,
    job_directory: Path | None,
    job_id: str,
) -> None:
    try:
        record = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryRecordProtected(
            "Library metadata is unreadable; refusing cleanup"
        ) from exc

    if (
        not isinstance(record, dict)
        or record.get("job_id") != job_id
        or record.get("status") != "complete"
        or not isinstance(record.get("file"), str)
    ):
        raise LibraryRecordProtected(
            "Metadata is not a verified completed record for this job"
        )

    if job_directory is None:
        raise LibraryRecordProtected(
            "Completed metadata exists but its job directory is missing"
        )

    relative_audio = Path(record["file"])
    if relative_audio.is_absolute():
        raise LibraryRecordProtected("Completed record contains an absolute audio path")

    resolved_audio = (root / relative_audio).resolve()
    try:
        resolved_audio.relative_to(job_directory)
    except ValueError as exc:
        raise LibraryRecordProtected(
            "Completed record does not point inside this job directory"
        ) from exc


def _verify_cleanup_authority(
    guard_store: Any,
    run_config: RunConfig,
    job_id: str,
    claim_token: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    _require_same_run(guard_store, run_config)
    _require_admitted_job(guard_store, run_config, job_id)

    claim = _read_claim(guard_store, run_config, job_id)
    if claim is None or claim.get("claim_token") != claim_token:
        raise InvalidTerminalTransition("Cleanup claim token does not match")

    outcome = _read_outcome(guard_store, run_config, job_id, claim)
    return claim, outcome


def cleanup_job_files(
    audio_root: str | Path,
    job_id: str,
    *,
    guard_store: Any,
    run_config: RunConfig,
    claim_token: str,
    clock: Callable[[], float] = time.time,
    finalization_rollback: bool = False,
) -> tuple[str, ...]:
    """Remove only this admitted job's files after an authorized terminal outcome.

    Failed/cancelled cleanup requires a confirmed failed/cancelled result.
    Finalization rollback requires its unique finalizing claim token. Rollback
    first atomically claims the write-once `failed` result; it therefore loses
    if `complete` has already won. A completed outcome is never removable here.
    """
    canonical_job_id = _canonical_job_id(job_id)
    root = Path(audio_root).resolve()

    claim, outcome = _verify_cleanup_authority(
        guard_store,
        run_config,
        canonical_job_id,
        claim_token,
    )
    owner = claim["claim"]
    current_result = outcome["result"] if outcome is not None else None

    if owner == "finalizing" and finalization_rollback:
        # Atomic rollback decision. This competes with `complete` on the same
        # conditional-insert outcome key; exactly one outcome can win.
        record_terminal_result(
            guard_store,
            run_config,
            canonical_job_id,
            owner_claim="finalizing",
            claim_token=claim_token,
            result="failed",
            clock=clock,
        )
        claim, outcome = _verify_cleanup_authority(
            guard_store,
            run_config,
            canonical_job_id,
            claim_token,
        )
        current_result = outcome["result"] if outcome is not None else None
        if current_result != "failed":
            raise InvalidTerminalTransition("Finalization rollback was not recorded")

    elif owner == "failed":
        if current_result != "failed":
            raise InvalidTerminalTransition(
                "Failed-job cleanup requires a recorded failed outcome"
            )

    elif owner == "cancelled":
        if current_result != "cancelled":
            raise InvalidTerminalTransition(
                "Cancelled-job cleanup requires confirmed cancellation"
            )

    else:
        raise InvalidTerminalTransition(
            "Cleanup requires failed/cancelled ownership or explicit rollback"
        )

    metadata_path = _library_record_path(root, canonical_job_id)
    job_directory = _safe_job_directory(root, canonical_job_id)

    if metadata_path is not None:
        if owner != "finalizing" or not finalization_rollback:
            raise LibraryRecordProtected(
                "A library record exists; ordinary cleanup will preserve it"
            )
        _validate_rollback_record(
            metadata_path,
            root,
            job_directory,
            canonical_job_id,
        )

    removed: list[str] = []

    # Remove metadata first: an interrupted cleanup cannot leave a visible
    # library entry pointing to audio that has already been removed.
    if metadata_path is not None:
        metadata_path.unlink()
        removed.append(f"library/{canonical_job_id}.json")

    if job_directory is not None:
        shutil.rmtree(job_directory)
        removed.append(f"jobs/{canonical_job_id}")

    return tuple(removed)

def read_run_config_identity(guard_store: Any) -> RunConfig:
    """Read and validate the immutable sentinel identity without expiry checks.

    Use only for already-admitted jobs, cancellation and terminal-state
    reconciliation. New admission must continue to use validate_run_config()
    through claim_one_attempt().
    """
    return _read_run_config_identity(guard_store)
