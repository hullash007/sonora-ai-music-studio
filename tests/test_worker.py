from __future__ import annotations

import concurrent.futures
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any

from sonora.guard import (
    RUN_CONFIG_KEY,
    RUN_CONFIG_KIND,
    RunConfig,
    claim_one_attempt,
    claim_terminal,
    read_run_config_identity,
    read_terminal_state,
    record_terminal_result,
    validate_run_config,
)
from sonora.library import read_track_record
from sonora.worker import (
    GenerationStopped,
    assert_job_active,
    cleanup_confirmed_cancelled_job,
    run_guarded_generation,
)


class FakeClock:
    def __init__(self, value: float = 1_800_000_000.0) -> None:
        self.value = value
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += seconds


class FakeGuardDict:
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


class WorkerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.guard = FakeGuardDict()
        issued_at = self.clock()

        self.guard.put(
            RUN_CONFIG_KEY,
            {
                "kind": RUN_CONFIG_KIND,
                "schema_version": 1,
                "run_id": str(uuid.uuid4()),
                "issued_at": issued_at,
                "expires_at": issued_at + 3600,
                "max_attempts": 1,
            },
            skip_if_exists=True,
        )

        self.run_config = validate_run_config(
            self.guard,
            clock=self.clock,
            minimum_remaining_seconds=0,
        )

        self.job_id = str(uuid.uuid4())
        claim_one_attempt(
            self.guard,
            self.job_id,
            clock=self.clock,
            minimum_remaining_seconds=0,
        )

        self.temp_dir = tempfile.TemporaryDirectory(prefix="sonora-worker-test-")
        self.audio_root = Path(self.temp_dir.name)
        self.job_dir = self.audio_root / "jobs" / self.job_id
        self.job_dir.mkdir(parents=True)

        self.commit_calls = 0

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def commit_audio(self) -> None:
        self.commit_calls += 1

    def create_fake_audio(self, name: str = "fixture.mp3") -> Path:
        output = self.job_dir / name
        output.write_bytes(b"fake-sonora-audio")
        return output

    def run_worker(self, generate_audio, *, commit_audio=None):
        return run_guarded_generation(
            guard_store=self.guard,
            run_config=self.run_config,
            job_id=self.job_id,
            audio_root=self.audio_root,
            prompt="Test prompt",
            lyrics="",
            duration_seconds=30,
            generate_audio=generate_audio,
            commit_audio=commit_audio or self.commit_audio,
            engine="Fake engine",
            model_revision="test-revision",
            created_at=self.clock(),
            clock=self.clock,
        )


class WorkerStateTests(WorkerFixture):
    def test_assert_active_passes_without_terminal_claim(self) -> None:
        assert_job_active(
            self.guard,
            self.run_config,
            self.job_id,
        )

    def test_assert_active_raises_after_cancellation_claim(self) -> None:
        claim_terminal(
            self.guard,
            self.run_config,
            self.job_id,
            "cancelled",
            clock=self.clock,
        )

        with self.assertRaises(GenerationStopped):
            assert_job_active(
                self.guard,
                self.run_config,
                self.job_id,
            )

    def test_worker_does_not_start_if_cancellation_already_won(self) -> None:
        claim_terminal(
            self.guard,
            self.run_config,
            self.job_id,
            "cancelled",
            clock=self.clock,
        )

        called = False

        def generate():
            nonlocal called
            called = True
            return self.create_fake_audio()

        result = self.run_worker(generate)

        self.assertFalse(called)
        self.assertEqual(result["status"], "cancelling")
        self.assertFalse(result["published"])
        self.assertEqual(self.commit_calls, 0)

    def test_generation_callback_can_stop_when_cancellation_arrives(self) -> None:
        def generate():
            raise GenerationStopped("cancelling")

        result = self.run_worker(generate)

        self.assertEqual(result["status"], "cancelling")
        self.assertFalse(result["published"])
        self.assertEqual(self.commit_calls, 0)

    def test_expired_admission_window_does_not_block_admitted_worker(self) -> None:
        self.clock.advance(3601)
        result = self.run_worker(self.create_fake_audio)

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["published"])


class WorkerFinalizationTests(WorkerFixture):
    def test_success_claims_finalizing_before_library_write(self) -> None:
        result = self.run_worker(self.create_fake_audio)

        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["published"])

        record = read_track_record(self.audio_root, self.job_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["duration_seconds"], 30)
        self.assertEqual(self.commit_calls, 1)

        state = read_terminal_state(
            self.guard,
            self.run_config,
            self.job_id,
        )
        self.assertEqual(state["state"], "complete")

    def test_cancel_winning_during_generation_prevents_publication(self) -> None:
        def generate():
            output = self.create_fake_audio()

            claim_terminal(
                self.guard,
                self.run_config,
                self.job_id,
                "cancelled",
                clock=self.clock,
            )
            return output

        result = self.run_worker(generate)

        self.assertEqual(result["status"], "cancelling")
        self.assertFalse(result["published"])
        self.assertIsNone(read_track_record(self.audio_root, self.job_id))
        self.assertTrue((self.job_dir / "fixture.mp3").exists())
        self.assertEqual(self.commit_calls, 0)

    def test_finalizing_claim_winning_prevents_cancellation_from_winning(self) -> None:
        finalizing_token: list[str] = []

        def generate():
            finalizing_token.append(
                claim_terminal(
                    self.guard,
                    self.run_config,
                    self.job_id,
                    "finalizing",
                    clock=self.clock,
                )
            )
            return self.create_fake_audio()

        result = self.run_worker(generate)

        self.assertEqual(result["status"], "finalizing")
        self.assertFalse(result["published"])

        cancel_token = claim_terminal(
            self.guard,
            self.run_config,
            self.job_id,
            "cancelled",
            clock=self.clock,
        )
        self.assertIsNone(cancel_token)

    def test_generation_failure_records_failed_and_cleans_partial_files(self) -> None:
        def generate():
            self.create_fake_audio()
            raise RuntimeError("simulated generation failure")

        result = self.run_worker(generate)

        self.assertEqual(result["status"], "failed")
        self.assertIn("simulated generation failure", result["error"])
        self.assertFalse(self.job_dir.exists())
        self.assertIsNone(read_track_record(self.audio_root, self.job_id))
        self.assertEqual(self.commit_calls, 1)

        state = read_terminal_state(
            self.guard,
            self.run_config,
            self.job_id,
        )
        self.assertEqual(state["state"], "failed")

    def test_cancel_winning_against_generation_failure_prevents_worker_cleanup(self) -> None:
        def generate():
            self.create_fake_audio()
            claim_terminal(
                self.guard,
                self.run_config,
                self.job_id,
                "cancelled",
                clock=self.clock,
            )
            raise RuntimeError("generation was stopped by cancellation")

        result = self.run_worker(generate)

        self.assertEqual(result["status"], "cancelling")
        self.assertTrue(self.job_dir.exists())
        self.assertTrue((self.job_dir / "fixture.mp3").exists())
        self.assertEqual(self.commit_calls, 0)

    def test_volume_commit_failure_rolls_back_unpublished_finalization(self) -> None:
        commits = 0

        def flaky_commit():
            nonlocal commits
            commits += 1
            if commits == 1:
                raise OSError("simulated Volume commit failure")

        result = self.run_worker(
            self.create_fake_audio,
            commit_audio=flaky_commit,
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.job_dir.exists())
        self.assertIsNone(read_track_record(self.audio_root, self.job_id))
        self.assertEqual(commits, 2)

        state = read_terminal_state(
            self.guard,
            self.run_config,
            self.job_id,
        )
        self.assertEqual(state["state"], "failed")

    def test_completed_library_files_are_not_cleaned(self) -> None:
        result = self.run_worker(self.create_fake_audio)

        self.assertEqual(result["status"], "complete")
        record = read_track_record(self.audio_root, self.job_id)
        self.assertIsNotNone(record)

        audio_file = self.audio_root / record["file"]
        self.assertTrue(audio_file.exists())
        self.assertTrue((self.audio_root / "library" / f"{self.job_id}.json").exists())


class ConfirmedCancellationCleanupTests(WorkerFixture):
    def test_cleanup_requires_confirmed_cancelled_outcome(self) -> None:
        self.create_fake_audio()
        token = claim_terminal(
            self.guard,
            self.run_config,
            self.job_id,
            "cancelled",
            clock=self.clock,
        )

        with self.assertRaises(RuntimeError):
            cleanup_confirmed_cancelled_job(
                guard_store=self.guard,
                run_config=self.run_config,
                job_id=self.job_id,
                claim_token=token,
                audio_root=self.audio_root,
                commit_audio=self.commit_audio,
                clock=self.clock,
            )

        self.assertTrue(self.job_dir.exists())

    def test_confirmed_cancellation_cleans_only_its_own_partial_directory(self) -> None:
        self.create_fake_audio()
        token = claim_terminal(
            self.guard,
            self.run_config,
            self.job_id,
            "cancelled",
            clock=self.clock,
        )

        record_terminal_result(
            self.guard,
            self.run_config,
            self.job_id,
            owner_claim="cancelled",
            claim_token=token,
            result="cancelled",
            clock=self.clock,
        )

        sibling_id = str(uuid.uuid4())
        sibling = self.audio_root / "jobs" / sibling_id
        sibling.mkdir()
        (sibling / "keep.mp3").write_bytes(b"keep")

        removed = cleanup_confirmed_cancelled_job(
            guard_store=self.guard,
            run_config=self.run_config,
            job_id=self.job_id,
            claim_token=token,
            audio_root=self.audio_root,
            commit_audio=self.commit_audio,
            clock=self.clock,
        )

        self.assertIn(f"jobs/{self.job_id}", removed)
        self.assertFalse(self.job_dir.exists())
        self.assertTrue((sibling / "keep.mp3").exists())
        self.assertEqual(self.commit_calls, 1)

    def test_wrong_cancellation_token_cannot_clean(self) -> None:
        self.create_fake_audio()
        token = claim_terminal(
            self.guard,
            self.run_config,
            self.job_id,
            "cancelled",
            clock=self.clock,
        )
        record_terminal_result(
            self.guard,
            self.run_config,
            self.job_id,
            owner_claim="cancelled",
            claim_token=token,
            result="cancelled",
            clock=self.clock,
        )

        with self.assertRaises(Exception):
            cleanup_confirmed_cancelled_job(
                guard_store=self.guard,
                run_config=self.run_config,
                job_id=self.job_id,
                claim_token=uuid.uuid4().hex,
                audio_root=self.audio_root,
                commit_audio=self.commit_audio,
                clock=self.clock,
            )

        self.assertTrue(self.job_dir.exists())


if __name__ == "__main__":
    unittest.main()
