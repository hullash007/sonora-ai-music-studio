"""Append-only local protocol helpers. No remote APIs, retries of side effects,
claim deletion, automatic rearming or model imports live here.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sonora.guard import (GuardUnavailable, RunConfig, _get, _put,
                          _require_same_run, _require_admitted_job)


class DefinitiveSubmissionRejected(RuntimeError):
    """Adapter guarantees no remote call was created, not a generic RPC error."""


def request_fingerprint(prompt: str, lyrics: str, duration: int) -> str:
    body = json.dumps({"prompt": prompt, "lyrics": lyrics, "duration": duration},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def receipt_key(config: RunConfig, job_id: str, kind: str) -> str:
    return f"sonora:run:{config.run_id}:job:{job_id}:{kind}"


def read_receipt(store: Any, config: RunConfig, job_id: str, kind: str):
    _require_same_run(store, config)
    _require_admitted_job(store, config, job_id)
    record = _get(store, receipt_key(config, job_id, kind))
    if record is not None and (not isinstance(record, dict)
                              or record.get("run_id") != config.run_id
                              or record.get("job_id") != job_id):
        raise GuardUnavailable("Recovery receipt identity is invalid")
    return record


def append_receipt(store: Any, config: RunConfig, job_id: str, kind: str,
                   **fields: Any) -> bool:
    """Return True ONLY for positively acknowledged fresh insertion.

    Read-back after lost acknowledgement may verify evidence, but returns False:
    it can never grant permission to repeat spawn/cancel/cleanup.
    """
    _require_same_run(store, config)
    _require_admitted_job(store, config, job_id)
    value = {**fields, "run_id": config.run_id, "job_id": job_id}
    key = receipt_key(config, job_id, kind)
    try:
        inserted = _put(store, key, value, skip_if_exists=True)
    except GuardUnavailable:
        if _get(store, key) == value:
            return False
        raise
    if inserted:
        _require_same_run(store, config)
        return True
    if _get(store, key) != value:
        raise GuardUnavailable("Conflicting immutable recovery receipt")
    return False


def submission_state(store: Any, config: RunConfig, job_id: str) -> str:
    """Derive uncertainty from durable evidence, never ordinary cache labels."""
    call = read_receipt(store, config, job_id, "function-call")
    if call is not None:
        if not isinstance(call.get("call_id"), str) or not call["call_id"].strip():
            raise GuardUnavailable("Function-call receipt has no valid call identity")
        return "submitted"
    if read_receipt(store, config, job_id, "dispatch-definitively-rejected"):
        return "submission_rejected"
    if read_receipt(store, config, job_id, "dispatch-started"):
        return "submission_unknown"
    if read_receipt(store, config, job_id, "pre-dispatch-failure"):
        return "pre_dispatch_failed"
    if read_receipt(store, config, job_id, "job-record-created"):
        return "ready_to_dispatch"
    return "admitted"


def confirm_receipt(store: Any, config: RunConfig, job_id: str, kind: str,
                    **fields: Any) -> None:
    """Bounded persistence of an OUTCOME, never permission to repeat an action.

    Both fresh insertion and exact existing/read-back evidence are sufficient.
    Raises if the outcome cannot be established after two attempts.
    """
    for attempt in range(2):
        try:
            append_receipt(store, config, job_id, kind, **fields)
            return
        except GuardUnavailable:
            if attempt == 1:
                raise
