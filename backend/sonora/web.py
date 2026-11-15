"""SONORA HTTP application factory.

This module is deliberately independent of Modal. Production staging and local
browser tests provide their own job store, generation callback and audio
storage dependencies.
"""

from __future__ import annotations

import hmac
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from sonora.guard import (
    AttemptAlreadyClaimed,
    GuardError,
    InvalidRunConfig,
    InvalidTerminalTransition,
    RunConfig,
    claim_one_attempt,
    claim_terminal,
    read_terminal_state,
    record_terminal_result,
)
from sonora.library import (
    list_track_records,
    read_track_record,
    resolve_audio_file,
)



def create_app(
    *,
    job_store: Any,
    enqueue_generation: Callable[[str, str, str, int], Any],
    audio_root: str | Path,
    reload_audio: Callable[[], Any],
    password: str,
    model_name: str = "ACE-Step 1.5",
    guard_store: Any | None = None,
    run_config_reader: Callable[[], RunConfig] | None = None,
    cancel_generation: Callable[[str], bool] | None = None,
    require_guard: bool = False,
    generation_enabled: bool = False,
    public_preview: bool = False,
    fixed_generation_duration_seconds: int | None = None,
    generation_termination_confirmed: Callable[[str], bool] | None = None,
    cleanup_cancelled_generation: Callable[[str, str, str], Any] | None = None,
    generation_call_state: Callable[[str], str] | None = None,
) -> FastAPI:
    """Create the SONORA API using injected storage and generation adapters.

    Args:
        job_store: Mapping-like object supporting get(), assignment and keys.
        enqueue_generation: Called as (job_id, prompt, lyrics, duration).
        audio_root: Root directory for generated audio and library metadata.
        reload_audio: Storage refresh callback; use a no-op for local files.
        password: Required password for authenticated API routes.
        model_name: Engine label shown in the library.
        guard_store: Optional durable store implementing atomic conditional put.
        run_config_reader: Reads the provisioned RunConfig identity without
            rejecting it merely because its admission window has expired.
        cancel_generation: Receives a persisted function-call ID. Returns True
            only when the cancellation request was accepted by the adapter.
        require_guard: Require all three guard dependencies. Set True for staging.
    This factory does not import or initialize Modal.
    """

    if require_guard and (not callable(generation_termination_confirmed) or not callable(cleanup_cancelled_generation)):
        raise ValueError("Guarded staging requires termination and cleanup adapters")

    if public_preview and generation_enabled:
        raise ValueError("Public preview cannot enable generation")
    root = Path(audio_root)
    app = FastAPI(
        title="SONORA Music Studio",
        docs_url=None if public_preview else "/docs",
        redoc_url=None if public_preview else "/redoc",
        openapi_url=None if public_preview else "/openapi.json",
    )
    if public_preview:
        @app.middleware("http")
        async def read_only_preview(request, call_next):
            if request.method not in {"GET", "HEAD"}:
                return JSONResponse(status_code=403, content={"detail": "Generation and changes are disabled in this preview"})
            path = request.url.path
            allowed = path in {"/", "/api/health", "/api/library", "/assets/app.js", "/assets/app.css"} or (
                re.fullmatch(r"/api/library/[a-zA-Z0-9-]+/audio", path) is not None
            )
            if not allowed:
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            head_request = request.method == "HEAD"
            if head_request:
                request.scope["method"] = "GET"
            response = await call_next(request)
            if head_request:
                async def empty_body():
                    if False:
                        yield b""
                response.body_iterator = empty_body()
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

    guard_enabled = bool(
        require_guard
        or guard_store is not None
        or run_config_reader is not None
        or cancel_generation is not None
    )
    if guard_enabled and (
        guard_store is None
        or not callable(run_config_reader)
        or not callable(cancel_generation)
    ):
        raise ValueError(
            "Guarded mode requires guard_store, run_config_reader and "
            "cancel_generation"
        )

    def _guard_job_key(config: RunConfig, job_id: str, suffix: str) -> str:
        return (
            f"sonora:run:{config.run_id}:job:{job_id}:{suffix}"
        )

    def _guard_get(key: str) -> Any:
        try:
            return guard_store.get(key)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Staging guard store is unavailable",
            ) from exc

    def _guard_put_once(key: str, value: dict[str, Any]) -> bool:
        try:
            result = guard_store.put(
                key,
                value,
                skip_if_exists=True,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Staging guard store is unavailable",
            ) from exc

        if type(result) is not bool:
            raise HTTPException(
                status_code=503,
                detail="Staging guard store returned an invalid result",
            )
        return result

    def _config_for_job(record: dict[str, Any]) -> RunConfig:
        """Read current sentinel identity and compare it with job admission."""
        stored = record.get("_guard_config")
        if not isinstance(stored, dict):
            raise HTTPException(
                status_code=503,
                detail="Job has no valid staging guard configuration",
            )

        try:
            expected = RunConfig(
                run_id=stored["run_id"],
                issued_at=stored["issued_at"],
                expires_at=stored["expires_at"],
                max_attempts=stored["max_attempts"],
            )
            actual = run_config_reader()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Staging run configuration is unavailable",
            ) from exc

        if not isinstance(actual, RunConfig) or actual != expected:
            raise HTTPException(
                status_code=503,
                detail="Staging run configuration no longer matches this job",
            )
        return actual

    def _save_job(job_id: str, **updates: Any) -> dict[str, Any]:
        current = job_store.get(job_id, {})
        if not isinstance(current, dict):
            current = {}
        updated = {**current, **updates}
        job_store[job_id] = updated
        return updated

    def _call_id_from_handle(handle: Any) -> str:
        call_id = handle if isinstance(handle, str) else getattr(
            handle, "object_id", None
        )
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError(
                "Generation adapter must return a call ID or an object "
                "with a non-empty object_id"
            )
        return call_id.strip()

    def _persist_call_id(
        config: RunConfig,
        job_id: str,
        call_id: str,
    ) -> None:
        key = _guard_job_key(config, job_id, "function-call")
        value = {
            "run_id": config.run_id,
            "job_id": job_id,
            "call_id": call_id,
        }
        inserted = _guard_put_once(key, value)
        if not inserted:
            existing = _guard_get(key)
            if existing != value:
                raise HTTPException(
                    status_code=503,
                    detail="A different function-call ID is already recorded",
                )

    def _process_pending_cancel(
        config: RunConfig,
        job_id: str,
    ) -> dict[str, Any]:
        """Reconcile a cancellation request once the queue handle is available."""
        request_key = _guard_job_key(config, job_id, "cancel-request")
        call_key = _guard_job_key(config, job_id, "function-call")
        dispatch_key = _guard_job_key(config, job_id, "cancel-dispatch")

        request = _guard_get(request_key)
        if not isinstance(request, dict):
            return {
                "job_id": job_id,
                "status": "cancelling",
                "cancellation_pending": True,
            }

        if (
            request.get("run_id") != config.run_id
            or request.get("job_id") != job_id
            or not isinstance(request.get("claim_token"), str)
        ):
            raise HTTPException(
                status_code=503,
                detail="Cancellation request record is malformed",
            )

        handle = _guard_get(call_key)
        if not isinstance(handle, dict):
            return {
                "job_id": job_id,
                "status": "cancelling",
                "cancellation_pending": True,
            }

        if (
            handle.get("run_id") != config.run_id
            or handle.get("job_id") != job_id
            or not isinstance(handle.get("call_id"), str)
            or not handle["call_id"].strip()
        ):
            raise HTTPException(
                status_code=503,
                detail="Generation handle record is malformed",
            )

        # Only one HTTP/queue-reconciliation path may dispatch cancellation.
        dispatch = {
            "run_id": config.run_id,
            "job_id": job_id,
            "claim_token": request["claim_token"],
        }
        if not _guard_put_once(dispatch_key, dispatch):
            state = read_terminal_state(guard_store, config, job_id)
            current = state["state"] if state else "cancelling"
            return {
                "job_id": job_id,
                "status": current,
                "cancellation_pending": current == "cancelling",
            }

        accepted = False
        cancellation_error = None
        try:
            accepted = cancel_generation(handle["call_id"])
            if type(accepted) is not bool:
                raise RuntimeError(
                    "Cancellation adapter must return a boolean result"
                )
        except Exception as exc:
            cancellation_error = str(exc)[:500]

        if accepted:
            dispatch_result_key = _guard_job_key(
                config,
                job_id,
                "cancel-dispatch-result",
            )

            _guard_put_once(
                dispatch_result_key,
                {
                    "run_id": config.run_id,
                    "job_id": job_id,
                    "accepted": True,
                    "accepted_at": time.time(),
                },
            )

            _save_job(
                job_id,
                status="cancelling",
                stage="Cancellation accepted; waiting for termination confirmation",
            )

            return {
                "job_id": job_id,
                "status": "cancellation_accepted",
                "cancellation_request_accepted": True,
                "termination_confirmed": False,
            }

        final_result = "cancellation_failed"
        try:
            record_terminal_result(
                guard_store,
                config,
                job_id,
                owner_claim="cancelled",
                claim_token=request["claim_token"],
                result=final_result,
            )
        except GuardError as exc:
            raise HTTPException(
                status_code=503,
                detail="Could not persist cancellation result",
            ) from exc

        _save_job(
            job_id,
            status=final_result,
            stage=(
                "Cancellation confirmed; worker call is terminal"
                if accepted
                else "Cancellation request failed"
            ),
            error=cancellation_error,
        )
        return {
            "job_id": job_id,
            "status": final_result,
            "cancellation_pending": False,
        }

    def confirm_cancelled_call(
        config: RunConfig,
        job_id: str,
    ) -> bool:
        request_key = _guard_job_key(config, job_id, "cancel-request")
        call_key = _guard_job_key(config, job_id, "function-call")
        dispatch_result_key = _guard_job_key(
            config,
            job_id,
            "cancel-dispatch-result",
        )

        request = _guard_get(request_key)
        call_record = _guard_get(call_key)
        dispatch_result = _guard_get(dispatch_result_key)

        if not (
            isinstance(request, dict)
            and request.get("run_id") == config.run_id
            and request.get("job_id") == job_id
            and isinstance(request.get("claim_token"), str)
        ):
            raise HTTPException(503, "Cancellation request record is invalid")

        if not (
            isinstance(call_record, dict)
            and call_record.get("run_id") == config.run_id
            and call_record.get("job_id") == job_id
            and isinstance(call_record.get("call_id"), str)
        ):
            return False

        if not (
            isinstance(dispatch_result, dict)
            and dispatch_result.get("run_id") == config.run_id
            and dispatch_result.get("job_id") == job_id
            and dispatch_result.get("accepted") is True
        ):
            return False

        if generation_termination_confirmed is None:
            raise HTTPException(503, "Termination polling is not configured")

        # False means the request was accepted but the function is still running.
        if not generation_termination_confirmed(call_record["call_id"]):
            return False

        # Write the final outcome only after Modal reports the call is terminal.
        record_terminal_result(
            guard_store,
            config,
            job_id,
            owner_claim="cancelled",
            claim_token=request["claim_token"],
            result="cancelled",
        )

        # One caller performs cleanup. The helper preserves completed records.
        cleanup_key = _guard_job_key(config, job_id, "cancel-cleanup")
        if cleanup_cancelled_generation is not None:
            if _guard_put_once(
                cleanup_key,
                {
                    "run_id": config.run_id,
                    "job_id": job_id,
                    "claimed_at": time.time(),
                },
            ):
                try:
                    cleanup_cancelled_generation(
                        job_id,
                        config.run_id,
                        request["claim_token"],
                    )
                except Exception:
                    # Preserve the confirmed cancellation. Cleanup is not
                    # retried automatically and its failure stays visible.
                    _save_job(job_id, cleanup_error="Cancellation confirmed; cleanup failed and requires review")

        return True

    def _mark_enqueue_failed(config: RunConfig, job_id: str, exc: Exception) -> None:
        """Claim failure if no earlier cancellation/finalization already won."""
        try:
            token = claim_terminal(
                guard_store,
                config,
                job_id,
                "failed",
            )
            if token is not None:
                record_terminal_result(
                    guard_store,
                    config,
                    job_id,
                    owner_claim="failed",
                    claim_token=token,
                    result="failed",
                )
        except GuardError:
            # Do not overwrite a competing cancellation claim. The guard
            # remains authoritative and will fail closed if it is unavailable.
            pass

        _save_job(
            job_id,
            status="failed",
            stage="Could not schedule generation worker",
            error=str(exc),
        )

    def verify_password(supplied: str | None) -> None:
        if public_preview:
            return
        if not password:
            raise HTTPException(
                status_code=503,
                detail="Staging portal password is not configured",
            )

        if not supplied or not hmac.compare_digest(supplied, password):
            raise HTTPException(
                status_code=401,
                detail="Incorrect staging studio password",
            )

    def find_job(job_id: str) -> tuple[str, dict[str, Any]] | None:
        """Find a job by canonical UUID, tolerating legacy UUID-hex keys."""
        try:
            canonical_id = str(uuid.UUID(job_id))
        except (ValueError, TypeError, AttributeError):
            return None

        record = job_store.get(canonical_id)
        if record is None:
            record = job_store.get(canonical_id.replace("-", ""))

        if not isinstance(record, dict):
            return None

        return canonical_id, record

    static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/assets/app.js")
    def frontend_js():
        return FileResponse(static_dir / "assets" / "app.js", media_type="text/javascript")

    @app.get("/assets/app.css")
    def frontend_css():
        return FileResponse(static_dir / "assets" / "app.css", media_type="text/css")

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        built_index = static_dir / "index.html"
        if built_index.is_file():
            return built_index.read_text(encoding="utf-8").replace(
                "__SONORA_PUBLIC_PREVIEW__", "true" if public_preview else "false"
            )
        raise HTTPException(status_code=503, detail="Studio assets are not built")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "SONORA HTTP API",
        }

    @app.post("/api/generate")
    def submit_generation(
        payload: dict[str, Any],
        x_portal_password: str | None = Header(default=None),
    ) -> dict[str, str]:
        verify_password(x_portal_password)

        prompt_value = payload.get("prompt", "")
        lyrics_value = payload.get("lyrics", "")
        raw_duration = payload.get("duration", 30)

        if not isinstance(prompt_value, str):
            raise HTTPException(status_code=400, detail="Prompt must be text")
        if not isinstance(lyrics_value, str):
            raise HTTPException(status_code=400, detail="Lyrics must be text")

        prompt = prompt_value.strip()
        lyrics = lyrics_value

        if not prompt:
            raise HTTPException(
                status_code=400,
                detail="A music prompt is required",
            )
        if len(prompt) > 512:
            raise HTTPException(
                status_code=400,
                detail="Prompt must be 512 characters or fewer",
            )
        if len(lyrics) > 4096:
            raise HTTPException(
                status_code=400,
                detail="Lyrics must be 4096 characters or fewer",
            )

        # Accept JSON integers only. This rejects booleans, floats and strings.
        if type(raw_duration) is not int:
            raise HTTPException(
                status_code=400,
                detail="Duration must be a whole-number integer",
            )

        duration = raw_duration
        if not 10 <= duration <= 480:
            raise HTTPException(
                status_code=400,
                detail="Duration must be between 10 and 480 seconds",
            )

        job_id = str(uuid.uuid4())
        created_at = time.time()

        if not generation_enabled:
            raise HTTPException(
                status_code=503,
                detail="Staging generation is disabled",
            )

        if (
            fixed_generation_duration_seconds is not None
            and duration != fixed_generation_duration_seconds
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "This staging run permits exactly "
                    f"{fixed_generation_duration_seconds} seconds"
                ),
            )

        run_config = None
        if guard_enabled:
            try:
                # Consumes the one-run admission atomically. It is never
                # released after queue failure, worker failure or cancellation.
                run_config = claim_one_attempt(guard_store, job_id)
            except AttemptAlreadyClaimed as exc:
                raise HTTPException(
                    status_code=409,
                    detail="The single staging generation attempt is already consumed",
                ) from exc
            except GuardError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Staging admission guard rejected the request",
                ) from exc

        job_record = {
            "job_id": job_id,
            "status": "queued",
            "stage": "Waiting for generation worker",
            "prompt": prompt,
            "lyrics": lyrics,
            "duration_seconds": duration,
            "created_at": created_at,
        }
        if run_config is not None:
            job_record["_guard_config"] = {
                "run_id": run_config.run_id,
                "issued_at": run_config.issued_at,
                "expires_at": run_config.expires_at,
                "max_attempts": run_config.max_attempts,
            }

        job_store[job_id] = job_record

        try:
            handle = enqueue_generation(job_id, prompt, lyrics, duration)
            if guard_enabled:
                try:
                    call_id = _call_id_from_handle(handle)
                    _persist_call_id(run_config, job_id, call_id)
                    # A cancel request may have arrived while enqueue_generation
                    # was waiting for the remote handle. Reconcile it now.
                    _process_pending_cancel(run_config, job_id)
                except Exception as exc:
                    _mark_enqueue_failed(run_config, job_id, exc)
                    if isinstance(exc, HTTPException):
                        raise
                    raise HTTPException(
                        status_code=503,
                        detail="Generation handle could not be safely recorded",
                    ) from exc
        except Exception as exc:
            if guard_enabled:
                _mark_enqueue_failed(run_config, job_id, exc)
            else:
                _save_job(
                    job_id,
                    status="failed",
                    stage="Could not schedule generation worker",
                    error=str(exc),
                )

            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code=503,
                detail=f"Could not schedule generation worker: {exc}",
            ) from exc

        response_status = "queued"
        if guard_enabled:
            state = read_terminal_state(guard_store, run_config, job_id)
            if state is not None:
                response_status = state["state"]

        return {
            "job_id": job_id,
            "status": response_status,
        }

    @app.get("/api/status/{job_id}")
    def get_status(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> dict[str, Any]:
        verify_password(x_portal_password)

        found = find_job(job_id)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail="Generation job not found",
            )

        canonical_id, record = found
        status = record.get("status", "queued")
        stage = record.get("stage", "Waiting for generation worker")
        error = record.get("error")

        recovery_required = False
        if guard_enabled:
            config = _config_for_job(record)
            try:
                terminal = read_terminal_state(
                    guard_store,
                    config,
                    canonical_id,
                )
            except GuardError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Staging job state is unavailable",
                ) from exc

            # Timeout/crash outside the synchronous callback may never update
            # ordinary job state. Reconcile a positively observed failed call,
            # but do not delete files here: terminal input is not proof that its
            # container has stopped. Interrupted finalization needs review.
            recovery_required = False
            if (terminal is None or terminal["state"] == "finalizing") and generation_call_state is not None:
                call_record = _guard_get(_guard_job_key(config, canonical_id, "function-call"))
                if isinstance(call_record, dict) and isinstance(call_record.get("call_id"), str):
                    observation = generation_call_state(call_record["call_id"])
                    if observation == "unknown":
                        recovery_required = True
                        stage = "Worker state unconfirmed; manual review required; partial files preserved"
                    elif observation == "failed":
                        if terminal is None:
                            _mark_enqueue_failed(config, canonical_id, RuntimeError("Remote worker terminated before completion; cleanup requires review"))
                            terminal = read_terminal_state(guard_store, config, canonical_id)
                            error = "Remote worker stopped before completion; partial files preserved for review"
                        else:
                            recovery_required = True
                            stage = "Worker stopped during finalization; manual recovery required"
            # Guard state wins over stale or late worker writes to job_store.
            if terminal is not None and terminal["state"] == "cancelling":
                if confirm_cancelled_call(config, canonical_id):
                    terminal = read_terminal_state(guard_store, config, canonical_id)
                else:
                    dispatch = _guard_get(_guard_job_key(config, canonical_id, "cancel-dispatch-result"))
                    if isinstance(dispatch, dict) and dispatch.get("accepted") is True:
                        terminal = {**terminal, "state": "cancellation_accepted"}
            if terminal is not None:
                status = terminal["state"]
                stage = {
                    "cancelling": "Cancellation requested; waiting for queue handle or worker reconciliation",
                    "cancelled": "Cancellation confirmed; worker call is terminal",
                    "cancellation_failed": "Cancellation request failed",
                    "finalizing": stage if recovery_required else "Finalizing generated audio",
                    "complete": "Music generation complete",
                    "failed": "Generation failed",
                }.get(status, stage)
                if status not in {"failed", "cancellation_failed"}:
                    error = None

        refreshed = job_store.get(canonical_id)
        if isinstance(refreshed, dict) and refreshed.get("cleanup_error"):
            error = refreshed["cleanup_error"]
        if recovery_required:
            status = "reconciliation_required"
        return {
            "job_id": canonical_id,
            "status": status,
            "stage": stage,
            "error": error,
            "termination_confirmed": status == "cancelled",
            "cancellation_request_accepted": status in {"cancelled", "cancellation_accepted"},
        }

    @app.post("/api/cancel/{job_id}")
    def cancel_job(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> JSONResponse:
        verify_password(x_portal_password)

        if not guard_enabled:
            raise HTTPException(
                status_code=503,
                detail="Cancellation is not configured for this app",
            )

        found = find_job(job_id)
        if found is None:
            raise HTTPException(status_code=404, detail="Generation job not found")

        canonical_id, record = found
        config = _config_for_job(record)

        try:
            token = claim_terminal(
                guard_store,
                config,
                canonical_id,
                "cancelled",
            )
            if token is None:
                current = read_terminal_state(
                    guard_store,
                    config,
                    canonical_id,
                )
                current_state = current["state"] if current else None
                if current_state == "cancelling":
                    return JSONResponse(
                        status_code=202,
                        content={
                            "job_id": canonical_id,
                            "status": "cancelling",
                            "cancellation_pending": True,
                        },
                    )
                raise HTTPException(
                    status_code=409,
                    detail=f"Job already has terminal state: {current_state or 'unknown'}",
                )

            request_key = _guard_job_key(config, canonical_id, "cancel-request")
            request = {
                "run_id": config.run_id,
                "job_id": canonical_id,
                "claim_token": token,
            }
            if not _guard_put_once(request_key, request):
                raise HTTPException(
                    status_code=503,
                    detail="Cancellation request could not be recorded",
                )

            outcome = _process_pending_cancel(config, canonical_id)
        except GuardError as exc:
            raise HTTPException(
                status_code=503,
                detail="Staging cancellation guard is unavailable",
            ) from exc

        if outcome["status"] in {"cancelling", "cancellation_accepted"}:
            return JSONResponse(status_code=202, content=outcome)
        if outcome["status"] == "cancellation_failed":
            return JSONResponse(status_code=502, content=outcome)
        return JSONResponse(status_code=200, content=outcome)

    @app.get("/api/library")
    def get_library(
        x_portal_password: str | None = Header(default=None),
    ) -> dict[str, Any]:
        verify_password(x_portal_password)
        reload_audio()

        try:
            records = list_track_records(root, limit=200)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not read the music library",
            ) from exc

        tracks = [
            {
                "job_id": item["job_id"],
                "title": item.get("title", "Untitled track"),
                "prompt": item.get("prompt", ""),
                "duration_seconds": item.get("duration_seconds"),
                "created_at": item.get("created_at"),
                "filename": item.get("filename") or Path(item["file"]).name,
                "engine": item.get("engine", model_name),
                "model_revision": item.get("model_revision"),
            }
            for item in records
        ]

        return {
            "tracks": tracks,
            "count": len(tracks),
        }

    def make_audio_response(
        job_id: str,
        supplied_password: str | None,
    ) -> FileResponse:
        verify_password(supplied_password)
        reload_audio()

        record = read_track_record(root, job_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Track not found in the music library",
            )

        try:
            file_path = resolve_audio_file(root, record["file"])
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=404,
                detail="Track audio file is unavailable",
            ) from exc

        requested_filename = record.get("filename")
        filename = (
            Path(requested_filename).name
            if isinstance(requested_filename, str) and requested_filename.strip()
            else file_path.name
        )

        media_type, _ = mimetypes.guess_type(filename)
        if not media_type:
            media_type = mimetypes.guess_type(file_path.name)[0] or "audio/mpeg"

        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=filename,
        )

    # The supplied HTML uses this route. The additional aliases preserve
    # compatibility with the earlier staging page and clients.
    @app.get("/api/library/{job_id}/audio")
    def library_audio(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> FileResponse:
        return make_audio_response(job_id, x_portal_password)

    @app.get("/api/audio/{job_id}")
    def stream_audio(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> FileResponse:
        return make_audio_response(job_id, x_portal_password)

    @app.get("/api/download/{job_id}")
    def download_audio(
        job_id: str,
        x_portal_password: str | None = Header(default=None),
    ) -> FileResponse:
        return make_audio_response(job_id, x_portal_password)

    return app
