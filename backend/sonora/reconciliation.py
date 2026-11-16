"""Pure, read-only lifecycle reduction over an already collected snapshot.

No store access or side effects. Missing evidence stays unknown. This reducer
is an operator explanation, never permission to dispatch, cancel or clean.
"""
from __future__ import annotations

from typing import Any


def reconcile_job(*, admission: dict | None, receipts: dict[str, Any],
                  terminal_claim: dict | None = None,
                  terminal_result: dict | None = None,
                  guard_available: bool = True,
                  worker_observation: str = "unknown",
                  storage_observation: str = "unknown") -> dict:
    result = {"state": "reconciliation_required", "confidence": "unresolved",
              "terminal_owner": None, "worker_observation": worker_observation,
              "storage_observation": storage_observation,
              "worker_termination_confirmed": False,
              "cleanup_state": "cleanup_not_started",
              "library_visibility": "hidden", "evidence": [],
              "allowed_actions": ["inspect_guard_receipts", "inspect_same_call_read_only",
                                  "inspect_storage_read_only"],
              "forbidden_actions": ["spawn", "cancel_again", "delete_audio",
                                    "rearm_admission", "recommit_audio"]}
    if not guard_available or not isinstance(admission, dict):
        return result
    run, job = admission.get("run_id"), admission.get("job_id")
    if not run or not job:
        return result
    records = [*receipts.values(), terminal_claim, terminal_result]
    if any(record is not None and (not isinstance(record, dict)
           or record.get("run_id") != run or record.get("job_id") != job)
           for record in records):
        return result
    present = {key for key, value in receipts.items() if value is not None}
    result["evidence"] = sorted(present)
    owner = terminal_claim.get("claim") if terminal_claim else None
    result["terminal_owner"] = owner
    if terminal_claim and (owner not in {"finalizing", "cancelled", "failed"}
                           or not terminal_claim.get("claim_token")):
        return result
    outcome = None
    if terminal_result:
        if not terminal_claim or (terminal_result.get("claim_token") != terminal_claim["claim_token"]
                or terminal_result.get("owner_claim") != owner):
            return result
        outcome = terminal_result.get("result")
        if outcome not in {"finalizing": {"complete", "failed"},
                           "cancelled": {"cancelled", "cancellation_failed"},
                           "failed": {"failed"}}[owner]:
            return result
    started = receipts.get("audio-commit-started")
    committed = receipts.get("audio-commit-completed")
    for record in (started, committed):
        if record and (owner != "finalizing" or record.get("claim_token") != terminal_claim["claim_token"]
                       or record.get("operation") != "audio-volume-v1"):
            return result
    if committed and not started:
        return result
    cleanup_started = receipts.get("cancel-cleanup")
    cleanup_done = receipts.get("cleanup-completed")
    if cleanup_done and not cleanup_started:
        return result
    if cleanup_started:
        result["cleanup_state"] = "cleanup_completed" if cleanup_done else "cleanup_outcome_unknown"
    terminal_observed = receipts.get("worker-terminal-observed")
    call = receipts.get("function-call")
    stop_confirmed = bool(terminal_observed and call and call.get("call_id")
                          and terminal_observed.get("call_id") == call["call_id"]
                          and terminal_observed.get("termination_confirmed") is True
                          and terminal_observed.get("zero_activity") is True
                          and terminal_claim and terminal_observed.get("claim_token") == terminal_claim["claim_token"])
    result["worker_termination_confirmed"] = stop_confirmed
    cancelled_evidence = bool(stop_confirmed and terminal_observed.get("state") == "input_cancelled")
    for record in (cleanup_started, cleanup_done):
        if record and (not terminal_claim or record.get("claim_token") != terminal_claim["claim_token"]
                       or not call or record.get("call_id") != call.get("call_id")
                       or record.get("operation") != "cancel-job-files-v1"):
            return result
    if cleanup_started and (outcome != "cancelled" or not cancelled_evidence):
        return result
    if outcome == "complete":
        if not started or not committed or storage_observation != "own_audio_verified":
            return result
        result.update(state="complete", confidence="confirmed", library_visibility="visible")
    elif outcome == "cancelled":
        if not cancelled_evidence:
            return result
        result.update(state=result["cleanup_state"] if cleanup_started else "cancelled",
                      confidence="confirmed" if cleanup_done or not cleanup_started else "unresolved")
    elif outcome == "cancellation_failed":
        result.update(state="cancellation_request_rejected", confidence="confirmed")
        # Request rejection says nothing about whether computation stopped.
    elif outcome == "failed":
        result.update(state=outcome, confidence="confirmed")
    elif owner == "finalizing":
        result["state"] = "audio_commit_unknown" if started and not committed else "finalizing"
    elif owner == "cancelled":
        if stop_confirmed and not cancelled_evidence:
            result["state"] = "reconciliation_required"
            return result
        accepted = receipts.get("cancel-dispatch-result")
        result["state"] = ("cancellation_accepted" if accepted and accepted.get("accepted") is True
                           else "cancel_dispatch_unknown" if "cancel-dispatch" in present else "cancelling")
    elif call:
        result["state"] = "submitted" if isinstance(call.get("call_id"), str) and call["call_id"].strip() else "reconciliation_required"
    elif "dispatch-definitively-rejected" in present:
        result.update(state="submission_rejected", confidence="confirmed")
    elif "dispatch-started" in present:
        result["state"] = "submission_unknown"
    elif "pre-dispatch-failure" in present:
        result.update(state="pre_dispatch_failed", confidence="confirmed")
    else:
        result["state"] = "ready_to_dispatch" if "job-record-created" in present else "admitted"
    return result
