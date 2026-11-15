from __future__ import annotations

import tempfile
import concurrent.futures
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sonora.library import write_track_record
from sonora.web import create_app
import concurrent.futures
import threading
from sonora.guard import (
    claim_terminal,
    read_terminal_state,
    RUN_CONFIG_KEY,
    RUN_CONFIG_KIND,
    RunConfig,
    validate_run_config,
)




TEST_PASSWORD = "sonora-local-test-password"
AUTH_HEADERS = {"X-Portal-Password": TEST_PASSWORD}


class SonoraApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sonora-api-test-")
        self.audio_root = Path(self.temp_dir.name)
        self.job_store: dict[str, dict[str, Any]] = {}
        self.enqueue_calls: list[tuple[str, str, str, int]] = []
        self.reload_calls = 0
        self.enqueue_error: Exception | None = None

        def enqueue_generation(
            job_id: str,
            prompt: str,
            lyrics: str,
            duration: int,
        ) -> None:
            if self.enqueue_error is not None:
                raise self.enqueue_error

            self.enqueue_calls.append((job_id, prompt, lyrics, duration))

        def reload_audio() -> None:
            self.reload_calls += 1

        self.app = create_app(generation_enabled=True,
            job_store=self.job_store,
            enqueue_generation=enqueue_generation,
            audio_root=self.audio_root,
            reload_audio=reload_audio,
            password=TEST_PASSWORD,
        )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def create_library_track(
        self,
        *,
        job_id: str | None = None,
        filename: str = "test-track.mp3",
        created_at: float = 1_800_000_000.0,
    ) -> str:
        track_id = job_id or str(uuid.uuid4())
        relative_path = f"jobs/{track_id}/{filename}"
        audio_path = self.audio_root / relative_path
        audio_path.parent.mkdir(parents=True, exist_ok=True)

        # A small file fixture; these tests verify API delivery, not decoding.
        audio_path.write_bytes(b"ID3-sonora-test-audio")

        write_track_record(
            self.audio_root,
            {
                "job_id": track_id,
                "title": "Test track",
                "status": "complete",
                "prompt": "A test prompt",
                "lyrics": "",
                "filename": filename,
                "file": relative_path,
                "duration_seconds": 30,
                "created_at": created_at,
                "completed_at": created_at + 5,
                "engine": "ACE-Step 1.5",
                "model_revision": "test-revision",
            },
        )
        return track_id

    def test_home_returns_existing_studio_page(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("SONORA", response.text)
        self.assertIn('id="root"', response.text)
        self.assertIn('data-public-preview="false"', response.text)

    def test_health_endpoint_is_public(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_library_requires_password(self) -> None:
        response = self.client.get("/api/library")

        self.assertEqual(response.status_code, 401)

    def test_wrong_password_is_rejected(self) -> None:
        response = self.client.get(
            "/api/library",
            headers={"X-Portal-Password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 401)

    def test_generate_requires_password(self) -> None:
        response = self.client.post(
            "/api/generate",
            json={"prompt": "Ambient instrumental", "duration": 30},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.enqueue_calls), 0)

    def test_generate_queues_job_with_valid_payload(self) -> None:
        payload = {
            "prompt": "Ambient instrumental with soft piano",
            "lyrics": "",
            "duration": 45,
        }

        response = self.client.post(
            "/api/generate",
            json=payload,
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "queued")

        job_id = data["job_id"]
        self.assertEqual(str(uuid.UUID(job_id)), job_id)
        self.assertIn(job_id, self.job_store)

        self.assertEqual(
            self.enqueue_calls,
            [(job_id, payload["prompt"], "", 45)],
        )
        self.assertEqual(self.job_store[job_id]["status"], "queued")

    def test_status_returns_existing_job(self) -> None:
        job_id = str(uuid.uuid4())
        self.job_store[job_id.replace("-", "")] = {
            "job_id": job_id.replace("-", ""),
            "status": "running",
            "stage": "Generating music",
        }

        response = self.client.get(
            f"/api/status/{job_id}",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_id"], job_id)
        self.assertEqual(response.json()["status"], "running")
        self.assertEqual(response.json()["stage"], "Generating music")

    def test_status_returns_404_for_unknown_or_invalid_job(self) -> None:
        unknown_id = str(uuid.uuid4())

        unknown_response = self.client.get(
            f"/api/status/{unknown_id}",
            headers=AUTH_HEADERS,
        )
        invalid_response = self.client.get(
            "/api/status/not-a-uuid",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(unknown_response.status_code, 404)
        self.assertEqual(invalid_response.status_code, 404)

    def test_duration_rejects_non_integer_json_values(self) -> None:
        invalid_values = [30.5, 30.0, True, False, "30"]

        for value in invalid_values:
            with self.subTest(duration=value):
                response = self.client.post(
                    "/api/generate",
                    json={
                        "prompt": "Test prompt",
                        "duration": value,
                    },
                    headers=AUTH_HEADERS,
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("integer", response.json()["detail"])

        self.assertEqual(len(self.enqueue_calls), 0)

    def test_duration_rejects_values_outside_supported_range(self) -> None:
        for value in (9, 481, 0, -1):
            with self.subTest(duration=value):
                response = self.client.post(
                    "/api/generate",
                    json={
                        "prompt": "Test prompt",
                        "duration": value,
                    },
                    headers=AUTH_HEADERS,
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("between 10 and 480", response.json()["detail"])

        self.assertEqual(len(self.enqueue_calls), 0)

    def test_duration_defaults_to_30_seconds(self) -> None:
        response = self.client.post(
            "/api/generate",
            json={"prompt": "Test prompt"},
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.enqueue_calls[0][3], 30)

    def test_prompt_validation(self) -> None:
        invalid_payloads = [
            {},
            {"prompt": ""},
            {"prompt": "   "},
            {"prompt": 123},
            {"prompt": "x" * 513},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/api/generate",
                    json=payload,
                    headers=AUTH_HEADERS,
                )
                self.assertEqual(response.status_code, 400)

        self.assertEqual(len(self.enqueue_calls), 0)

    def test_lyrics_validation(self) -> None:
        invalid_payloads = [
            {"prompt": "Valid prompt", "lyrics": 123},
            {"prompt": "Valid prompt", "lyrics": "x" * 4097},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload_type=type(payload["lyrics"]).__name__):
                response = self.client.post(
                    "/api/generate",
                    json=payload,
                    headers=AUTH_HEADERS,
                )
                self.assertEqual(response.status_code, 400)

        self.assertEqual(len(self.enqueue_calls), 0)

    def test_generation_enqueue_error_marks_job_failed(self) -> None:
        self.enqueue_error = RuntimeError("Test queue failure")

        response = self.client.post(
            "/api/generate",
            json={"prompt": "Test prompt", "duration": 30},
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.job_store), 1)

        failed_job = next(iter(self.job_store.values()))
        self.assertEqual(failed_job["status"], "failed")
        self.assertIn("Test queue failure", failed_job["error"])

    def test_library_lists_completed_tracks(self) -> None:
        track_id = self.create_library_track()

        response = self.client.get(
            "/api/library",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["count"], 1)
        self.assertEqual(data["tracks"][0]["job_id"], track_id)
        self.assertEqual(data["tracks"][0]["title"], "Test track")
        self.assertEqual(data["tracks"][0]["filename"], "test-track.mp3")
        self.assertEqual(self.reload_calls, 1)

    def test_library_is_sorted_newest_first(self) -> None:
        older_id = self.create_library_track(
            created_at=1_700_000_000.0,
        )
        newer_id = self.create_library_track(
            created_at=1_800_000_000.0,
        )

        response = self.client.get(
            "/api/library",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        ids = [item["job_id"] for item in response.json()["tracks"]]
        self.assertEqual(ids, [newer_id, older_id])

    def test_audio_route_returns_track_file(self) -> None:
        track_id = self.create_library_track()

        response = self.client.get(
            f"/api/library/{track_id}/audio",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ID3-sonora-test-audio")
        self.assertIn("audio/mpeg", response.headers["content-type"])
        self.assertEqual(self.reload_calls, 1)

    def test_audio_route_requires_password(self) -> None:
        track_id = self.create_library_track()

        response = self.client.get(
            f"/api/library/{track_id}/audio",
        )

        self.assertEqual(response.status_code, 401)

    def test_audio_route_returns_404_for_unknown_track(self) -> None:
        response = self.client.get(
            f"/api/library/{uuid.uuid4()}/audio",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 404)

    def test_audio_route_rejects_invalid_uuid(self) -> None:
        response = self.client.get(
            "/api/library/not-a-uuid/audio",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 404)


    def test_duration_accepts_inclusive_boundaries(self) -> None:
        for duration in (10, 480):
            response = self.client.post("/api/generate", json={"prompt": "Boundary", "duration": duration}, headers=AUTH_HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.enqueue_calls[-1][3], duration)

    def test_status_requires_authentication(self) -> None:
        self.assertEqual(self.client.get(f"/api/status/{uuid.uuid4()}").status_code, 401)

    def test_status_states_and_no_private_traceback(self) -> None:
        for state in ("queued", "complete", "failed"):
            job_id = str(uuid.uuid4())
            self.job_store[job_id] = {"status": state, "traceback": "private", "error": "test" if state == "failed" else None}
            response = self.client.get(f"/api/status/{job_id}", headers=AUTH_HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], state)
            self.assertNotIn("traceback", response.json())

    def test_unconfigured_password_fails_closed(self) -> None:
        app = create_app(generation_enabled=True, job_store={}, enqueue_generation=lambda *args: None,
                         audio_root=self.audio_root, reload_audio=lambda: None, password="")
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/library", headers=AUTH_HEADERS).status_code, 503)

    def test_missing_or_traversal_audio_fails_closed(self) -> None:
        import json
        for path in ("missing.mp3", "../outside.mp3", str(self.audio_root.parent / "outside.mp3")):
            track_id = self.create_library_track()
            metadata = self.audio_root / "library" / f"{track_id}.json"
            record = json.loads(metadata.read_text())
            record["file"] = path
            metadata.write_text(json.dumps(record))
            response = self.client.get(f"/api/library/{track_id}/audio", headers=AUTH_HEADERS)
            self.assertEqual(response.status_code, 404)

    def test_wav_delivery_and_audio_aliases(self) -> None:
        track_id = self.create_library_track(filename="fixture.wav")
        for route in (f"/api/library/{track_id}/audio", f"/api/audio/{track_id}", f"/api/download/{track_id}"):
            response = self.client.get(route, headers=AUTH_HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertIn("audio/", response.headers["content-type"])
            self.assertIn("fixture.wav", response.headers["content-disposition"])


class FakeAtomicGuardStore:
    """Thread-safe local fake for the guard's required conditional-put API."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        with self.lock:
            return self.data.get(key, default)

    def put(
        self,
        key: str,
        value: Any,
        *,
        skip_if_exists: bool = False,
    ) -> bool:
        with self.lock:
            if skip_if_exists:
                if key in self.data:
                    return False
                self.data[key] = value
                return True
            self.data[key] = value
            return True


class GuardedSonoraApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sonora-guarded-api-")
        self.audio_root = Path(self.temp_dir.name)
        self.guard_store = FakeAtomicGuardStore()
        self.job_store: dict[str, dict[str, Any]] = {}
        self.enqueue_calls: list[tuple[str, str, str, int]] = []
        self.cancel_calls: list[str] = []
        self.cancel_result = True
        self.termination_confirmed = True
        self.call_state = "pending"
        self.cleanup_calls = []
        self.cleanup_error = None
        import time
        self.now = time.time()

        self.guard_store.put(
            RUN_CONFIG_KEY,
            {
                "kind": RUN_CONFIG_KIND,
                "schema_version": 1,
                "run_id": str(uuid.uuid4()),
                "issued_at": self.now,
                "expires_at": self.now + 3600,
                "max_attempts": 1,
            },
            skip_if_exists=True,
        )
        self.run_config = validate_run_config(
            self.guard_store,
            clock=lambda: self.now,
            minimum_remaining_seconds=0,
        )

        def enqueue(job_id: str, prompt: str, lyrics: str, duration: int) -> str:
            self.enqueue_calls.append((job_id, prompt, lyrics, duration))
            return f"fake-call-{job_id}"

        def cancel(call_id: str) -> bool:
            self.cancel_calls.append(call_id)
            return self.cancel_result

        def cleanup(*args):
            self.cleanup_calls.append(args)
            if self.cleanup_error:
                raise self.cleanup_error

        self.cleanup = cleanup
        self.enqueue = enqueue
        self.cancel = cancel
        self.app = self.make_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def make_app(self, *, enqueue=None, cancel=None):
        return create_app(generation_enabled=True,
            job_store=self.job_store,
            enqueue_generation=enqueue or self.enqueue,
            audio_root=self.audio_root,
            reload_audio=lambda: None,
            password=TEST_PASSWORD,
            guard_store=self.guard_store,
            run_config_reader=lambda: self.run_config,
            cancel_generation=cancel or self.cancel,
            require_guard=True,
            generation_termination_confirmed=lambda call_id: self.termination_confirmed,
            generation_call_state=lambda call_id: self.call_state,
            cleanup_cancelled_generation=self.cleanup,
        )

    def submit(self, client: TestClient | None = None):
        active_client = client or self.client
        return active_client.post(
            "/api/generate",
            json={
                "prompt": "A short test prompt",
                "lyrics": "",
                "duration": 30,
            },
            headers=AUTH_HEADERS,
        )

    def test_remote_timeout_reconciles_failed_without_deleting_files(self) -> None:
        response = self.submit()
        job_id = response.json()["job_id"]
        self.call_state = "failed"
        status = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "failed")
        self.assertEqual(self.cleanup_calls, [])
        self.assertEqual(self.submit().status_code, 409)

    def test_ambiguous_remote_state_does_not_claim_failure(self) -> None:
        response = self.submit()
        job_id = response.json()["job_id"]
        status = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        self.assertEqual(status.json()["status"], "queued")
        self.assertEqual(self.cleanup_calls, [])

    def test_cleanup_error_preserves_cancelled_and_reports_review(self) -> None:
        job_id = self.submit().json()["job_id"]
        self.termination_confirmed = False
        self.client.post("/api/cancel/" + job_id, headers=AUTH_HEADERS)
        self.termination_confirmed = True
        self.cleanup_error = RuntimeError("cleanup disk failure")
        first = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        second = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()["status"], "cancelled")
        self.assertIn("cleanup failed", second.json()["error"])
        self.assertEqual(len(self.cleanup_calls), 1)

    def test_unknown_remote_state_requests_review_without_terminal_claim(self) -> None:
        response = self.submit()
        job_id = response.json()["job_id"]
        self.call_state = "unknown"
        status = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        self.assertEqual(status.json()["status"], "reconciliation_required")
        self.assertEqual(self.cleanup_calls, [])
        self.assertIsNone(read_terminal_state(self.guard_store, self.run_config, job_id))

    def test_interrupted_finalization_requests_review_without_cleanup(self) -> None:
        response = self.submit()
        job_id = response.json()["job_id"]
        claim_terminal(self.guard_store, self.run_config, job_id, "finalizing")
        self.call_state = "failed"
        status = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        self.assertEqual(status.json()["status"], "reconciliation_required")
        self.assertIn("manual recovery", status.json()["stage"])
        self.assertEqual(self.cleanup_calls, [])

    def test_guarded_status_rejects_missing_job_identity(self) -> None:
        response = self.submit()
        job_id = response.json()["job_id"]
        self.job_store[job_id].pop("_guard_config")
        self.job_store[job_id]["status"] = "complete"
        status = self.client.get("/api/status/" + job_id, headers=AUTH_HEADERS)
        self.assertEqual(status.status_code, 503)

    def test_guard_is_required_when_staging_mode_is_required(self) -> None:
        with self.assertRaises(ValueError):
            create_app(generation_enabled=True,
                job_store={},
                enqueue_generation=lambda *args: "unused",
                audio_root=self.audio_root,
                reload_audio=lambda: None,
                password=TEST_PASSWORD,
                require_guard=True,
            generation_termination_confirmed=lambda call_id: self.termination_confirmed,
            generation_call_state=lambda call_id: self.call_state,
            cleanup_cancelled_generation=self.cleanup,
            )

    def test_guarded_generation_persists_call_id_and_consumes_one_attempt(self) -> None:
        first = self.submit()
        self.assertEqual(first.status_code, 200)
        job_id = first.json()["job_id"]

        call_records = [
            value for key, value in self.guard_store.data.items()
            if key.endswith(":function-call")
        ]
        self.assertEqual(len(call_records), 1)
        self.assertEqual(call_records[0]["call_id"], f"fake-call-{job_id}")

        second = self.submit()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_cancel_requires_authentication(self) -> None:
        response = self.client.post(f"/api/cancel/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 401)

    def test_cancel_after_handle_is_persisted(self) -> None:
        submitted = self.submit()
        job_id = submitted.json()["job_id"]

        response = self.client.post(
            f"/api/cancel/{job_id}",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "cancellation_accepted")
        self.assertFalse(response.json()["termination_confirmed"])
        status = self.client.get(f"/api/status/{job_id}", headers=AUTH_HEADERS)
        self.assertEqual(status.json()["status"], "cancelled")
        self.assertNotIn("claim_token", response.json())
        self.assertEqual(self.cancel_calls, [f"fake-call-{job_id}"])

    def test_cancel_before_handle_returns_pending_then_enqueue_reconciles(self) -> None:
        entered_enqueue = threading.Event()
        release_enqueue = threading.Event()

        def delayed_enqueue(
            job_id: str,
            prompt: str,
            lyrics: str,
            duration: int,
        ) -> str:
            self.enqueue_calls.append((job_id, prompt, lyrics, duration))
            entered_enqueue.set()
            if not release_enqueue.wait(timeout=5):
                raise TimeoutError("test enqueue release timed out")
            return f"delayed-call-{job_id}"

        app = self.make_app(enqueue=delayed_enqueue)
        with TestClient(app) as submit_client, TestClient(app) as cancel_client:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending_submit = pool.submit(lambda: self.submit(submit_client))
                self.assertTrue(entered_enqueue.wait(timeout=5))

                job_id = self.enqueue_calls[0][0]
                pending_cancel = cancel_client.post(
                    f"/api/cancel/{job_id}",
                    headers=AUTH_HEADERS,
                )

                self.assertEqual(pending_cancel.status_code, 202)
                self.assertEqual(pending_cancel.json()["status"], "cancelling")
                self.assertTrue(pending_cancel.json()["cancellation_pending"])
                self.assertEqual(self.cancel_calls, [])

                release_enqueue.set()
                completed_submit = pending_submit.result(timeout=5)

            self.assertEqual(completed_submit.status_code, 200)
            self.assertEqual(completed_submit.json()["status"], "cancelling")
            self.assertEqual(self.cancel_calls, [f"delayed-call-{job_id}"])

            status = cancel_client.get(
                f"/api/status/{job_id}",
                headers=AUTH_HEADERS,
            )
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["status"], "cancelled")

    def test_cancellation_failure_is_not_reported_as_cancelled(self) -> None:
        submitted = self.submit()
        job_id = submitted.json()["job_id"]
        self.cancel_result = False

        response = self.client.post(
            f"/api/cancel/{job_id}",
            headers=AUTH_HEADERS,
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["status"], "cancellation_failed")

        status = self.client.get(
            f"/api/status/{job_id}",
            headers=AUTH_HEADERS,
        )
        self.assertEqual(status.json()["status"], "cancellation_failed")

    def test_guard_state_overrides_stale_worker_job_store_update(self) -> None:
        submitted = self.submit()
        job_id = submitted.json()["job_id"]
        self.client.post(f"/api/cancel/{job_id}", headers=AUTH_HEADERS)

        # Simulate a late/stale worker write into the ordinary job record.
        self.job_store[job_id] = {
            **self.job_store[job_id],
            "status": "complete",
            "stage": "Late worker update",
        }

        status = self.client.get(
            f"/api/status/{job_id}",
            headers=AUTH_HEADERS,
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "cancelled")
        self.assertNotEqual(status.json()["stage"], "Late worker update")

    def test_failed_or_cancelled_attempt_cannot_enqueue_another_job(self) -> None:
        first = self.submit()
        job_id = first.json()["job_id"]

        self.client.post(
            f"/api/cancel/{job_id}",
            headers=AUTH_HEADERS,
        )
        second = self.submit()

        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(self.enqueue_calls), 1)

    def test_missing_sentinel_fails_closed_for_new_admission(self) -> None:
        self.guard_store.data.pop(RUN_CONFIG_KEY, None)

        response = self.submit()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.enqueue_calls, [])

    def test_missing_sentinel_fails_closed_for_cancel(self) -> None:
        submitted = self.submit()
        job_id = submitted.json()["job_id"]
        self.guard_store.data.pop(RUN_CONFIG_KEY, None)

        response = self.client.post(
            f"/api/cancel/{job_id}",
            headers=AUTH_HEADERS,
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.cancel_calls, [])



    def test_accepted_cancellation_is_not_confirmed_until_poll_terminal(self):
        self.termination_confirmed = False
        job_id = self.submit().json()["job_id"]
        response = self.client.post(f"/api/cancel/{job_id}", headers=AUTH_HEADERS)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "cancellation_accepted")
        status = self.client.get(f"/api/status/{job_id}", headers=AUTH_HEADERS)
        self.assertEqual(status.json()["status"], "cancellation_accepted")
        self.assertFalse(status.json()["termination_confirmed"])
        self.assertEqual(self.cleanup_calls, [])
        self.termination_confirmed = True
        self.call_state = "pending"
        status = self.client.get(f"/api/status/{job_id}", headers=AUTH_HEADERS)
        self.assertEqual(status.json()["status"], "cancelled")
        self.assertTrue(status.json()["termination_confirmed"])
        self.client.get(f"/api/status/{job_id}", headers=AUTH_HEADERS)
        self.assertEqual(len(self.cleanup_calls), 1)
        self.assertNotIn("claim_token", status.text)

    def test_default_off_does_not_consume_admission(self):
        app = create_app(job_store=self.job_store, enqueue_generation=self.enqueue,
                         audio_root=self.audio_root, reload_audio=lambda: None, password=TEST_PASSWORD)
        with TestClient(app) as client:
            response = client.post("/api/generate", json={"prompt": "test"}, headers=AUTH_HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.enqueue_calls, [])


if __name__ == "__main__":
    unittest.main()
