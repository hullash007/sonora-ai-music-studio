from __future__ import annotations

import concurrent.futures
import json
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any

from sonora.guard import (
    RUN_CONFIG_KEY,
    RUN_CONFIG_KIND,
    AttemptAlreadyClaimed,
    GuardUnavailable,
    InvalidRunConfig,
    InvalidTerminalTransition,
    LibraryRecordProtected,
    RunExpired,
    UnsafeCleanupPath,
    admission_key,
    claim_one_attempt,
    claim_terminal,
    cleanup_job_files,
    read_terminal_state,
    record_terminal_result,
    terminal_claim_key,
    terminal_result_key,
    validate_run_config,
)


class FakeClock:
    def __init__(self, initial: float = 1_800_000_000.0) -> None:
        self.value = initial
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += seconds


class ThreadSafeFakeDict:
    """In-memory fake with atomic conditional insertion."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.fail_get = False
        self.fail_put = False

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if self.fail_get:
                raise RuntimeError("simulated read failure")
            return self._data.get(key, default)

    def put(
        self,
        key: str,
        value: Any,
        *,
        skip_if_exists: bool = False,
    ) -> bool:
        with self._lock:
            if self.fail_put:
                raise RuntimeError("simulated write failure")

            if skip_if_exists:
                if key in self._data:
                    return False
                self._data[key] = value
                return True

            self._data[key] = value
            return True

    def overwrite_for_test(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def remove_for_test(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


def provision_test_sentinel(
    store: ThreadSafeFakeDict,
    clock: FakeClock,
    *,
    duration: int = 3600,
):
    """Test-only stand-in for separately provisioned immutable run config."""
    issued_at = clock()
    sentinel = {
        "kind": RUN_CONFIG_KIND,
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "issued_at": issued_at,
        "expires_at": issued_at + duration,
        "max_attempts": 1,
    }

    if not store.put(RUN_CONFIG_KEY, sentinel, skip_if_exists=True):
        raise AssertionError("Could not provision test sentinel")

    return validate_run_config(store, clock=clock, minimum_remaining_seconds=0)


class GuardFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = ThreadSafeFakeDict()
        self.config = provision_test_sentinel(self.store, self.clock)
        self.job_id = str(uuid.uuid4())

    def admit(self, job_id: str | None = None) -> str:
        job_id = job_id or self.job_id
        claim_one_attempt(
            self.store,
            job_id,
            clock=self.clock,
            minimum_remaining_seconds=0,
        )
        return job_id

    def claim(self, owner: str, job_id: str | None = None) -> str:
        token = claim_terminal(
            self.store,
            self.config,
            job_id or self.job_id,
            owner,  # type: ignore[arg-type]
            clock=self.clock,
        )
        self.assertIsNotNone(token)
        return token

    def result(self, owner: str, token: str, outcome: str, job_id: str | None = None) -> None:
        record_terminal_result(
            self.store,
            self.config,
            job_id or self.job_id,
            owner_claim=owner,  # type: ignore[arg-type]
            claim_token=token,
            result=outcome,  # type: ignore[arg-type]
            clock=self.clock,
        )


class SentinelAndAdmissionTests(GuardFixture):
    def test_valid_sentinel_loads(self) -> None:
        self.assertEqual(
            validate_run_config(self.store, clock=self.clock),
            self.config,
        )

    def test_missing_sentinel_fails_closed(self) -> None:
        self.store.remove_for_test(RUN_CONFIG_KEY)
        with self.assertRaises(InvalidRunConfig):
            validate_run_config(self.store, clock=self.clock)

    def test_missing_or_unavailable_store_fails_closed(self) -> None:
        with self.assertRaises(GuardUnavailable):
            validate_run_config(None, clock=self.clock)

        self.store.fail_get = True
        with self.assertRaises(GuardUnavailable):
            validate_run_config(self.store, clock=self.clock)

    def test_expired_sentinel_denies_new_admission(self) -> None:
        self.clock.advance(3600)
        with self.assertRaises(RunExpired):
            claim_one_attempt(
                self.store,
                self.job_id,
                clock=self.clock,
                minimum_remaining_seconds=0,
            )

    def test_expiry_window_over_one_hour_is_invalid(self) -> None:
        now = self.clock()
        self.store.overwrite_for_test(
            RUN_CONFIG_KEY,
            {
                "kind": RUN_CONFIG_KIND,
                "schema_version": 1,
                "run_id": str(uuid.uuid4()),
                "issued_at": now,
                "expires_at": now + 3601,
                "max_attempts": 1,
            },
        )
        with self.assertRaises(InvalidRunConfig):
            validate_run_config(self.store, clock=self.clock)

    def test_boolean_max_attempts_is_invalid(self) -> None:
        now = self.clock()
        self.store.overwrite_for_test(
            RUN_CONFIG_KEY,
            {
                "kind": RUN_CONFIG_KIND,
                "schema_version": 1,
                "run_id": str(uuid.uuid4()),
                "issued_at": now,
                "expires_at": now + 3600,
                "max_attempts": True,
            },
        )
        with self.assertRaises(InvalidRunConfig):
            validate_run_config(self.store, clock=self.clock)

    def test_future_issue_time_is_invalid(self) -> None:
        now = self.clock()
        self.store.overwrite_for_test(
            RUN_CONFIG_KEY,
            {
                "kind": RUN_CONFIG_KIND,
                "schema_version": 1,
                "run_id": str(uuid.uuid4()),
                "issued_at": now + 5,
                "expires_at": now + 3600,
                "max_attempts": 1,
            },
        )
        with self.assertRaises(InvalidRunConfig):
            validate_run_config(self.store, clock=self.clock)

    def test_concurrent_admission_has_exactly_one_winner(self) -> None:
        job_ids = [str(uuid.uuid4()) for _ in range(32)]

        def attempt(job_id: str) -> str:
            try:
                claim_one_attempt(
                    self.store,
                    job_id,
                    clock=self.clock,
                    minimum_remaining_seconds=0,
                )
                return "accepted"
            except AttemptAlreadyClaimed:
                return "rejected"

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(attempt, job_ids))

        self.assertEqual(outcomes.count("accepted"), 1)
        self.assertEqual(outcomes.count("rejected"), 31)

    def test_restart_preserves_admission_and_expired_sentinel_denies_new_work(self) -> None:
        self.admit()
        with self.assertRaises(AttemptAlreadyClaimed):
            claim_one_attempt(self.store, str(uuid.uuid4()), clock=self.clock,
                              minimum_remaining_seconds=0)
        self.clock.advance(7 * 24 * 3600 + 1)
        self.store.remove_for_test(admission_key(self.config.run_id))
        with self.assertRaises(RunExpired):
            claim_one_attempt(self.store, str(uuid.uuid4()), clock=self.clock,
                              minimum_remaining_seconds=0)

    def test_expiry_does_not_block_existing_job_cancel_or_status_read(self) -> None:
        self.admit()
        self.clock.advance(3601)

        token = self.claim("cancelled")
        self.result("cancelled", token, "cancelled")

        state = read_terminal_state(self.store, self.config, self.job_id)
        self.assertEqual(state["state"], "cancelled")
        self.assertNotIn("claim_token", state)

    def test_expiry_does_not_block_existing_job_failure_recording(self) -> None:
        self.admit()
        self.clock.advance(3601)

        token = self.claim("failed")
        self.result("failed", token, "failed")

        self.assertEqual(
            read_terminal_state(self.store, self.config, self.job_id)["state"],
            "failed",
        )

    def test_expiry_does_not_block_admitted_job_finalization(self) -> None:
        self.admit()
        self.clock.advance(3601)

        token = self.claim("finalizing")
        self.result("finalizing", token, "complete")

        self.assertEqual(
            read_terminal_state(self.store, self.config, self.job_id)["state"],
            "complete",
        )

    def test_missing_sentinel_blocks_existing_job_operations(self) -> None:
        self.admit()
        self.store.remove_for_test(RUN_CONFIG_KEY)

        with self.assertRaises(InvalidRunConfig):
            claim_terminal(
                self.store,
                self.config,
                self.job_id,
                "cancelled",
                clock=self.clock,
            )

        with self.assertRaises(InvalidRunConfig):
            read_terminal_state(self.store, self.config, self.job_id)


class TerminalConcurrencyTests(GuardFixture):
    def setUp(self) -> None:
        super().setUp()
        self.admit()

    def test_concurrent_terminal_claims_have_one_owner(self) -> None:
        owners = ["cancelled", "finalizing", "failed"] * 10

        def attempt(owner: str):
            return claim_terminal(
                self.store,
                self.config,
                self.job_id,
                owner,  # type: ignore[arg-type]
                clock=self.clock,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            tokens = list(pool.map(attempt, owners))

        winners = [token for token in tokens if token is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(winners[0]), 32)

    def test_token_is_unique_and_persisted(self) -> None:
        token = self.claim("finalizing")
        record = self.store.get(terminal_claim_key(self.config.run_id, self.job_id))
        self.assertEqual(record["claim_token"], token)
        self.assertEqual(uuid.UUID(hex=token).hex, token)

    def test_wrong_token_cannot_write_result(self) -> None:
        token = self.claim("finalizing")
        with self.assertRaises(InvalidTerminalTransition):
            self.result("finalizing", uuid.uuid4().hex, "complete")
        self.result("finalizing", token, "complete")

    def test_wrong_owner_cannot_write_result(self) -> None:
        token = self.claim("cancelled")
        with self.assertRaises(InvalidTerminalTransition):
            self.result("finalizing", token, "complete")

    def test_same_token_and_result_is_idempotent(self) -> None:
        token = self.claim("finalizing")
        self.result("finalizing", token, "complete")
        self.result("finalizing", token, "complete")
        self.assertEqual(
            read_terminal_state(self.store, self.config, self.job_id)["state"],
            "complete",
        )

    def test_conflicting_concurrent_results_are_write_once(self) -> None:
        token = self.claim("finalizing")
        barrier = threading.Barrier(2)

        def publish(outcome: str) -> str:
            barrier.wait()
            try:
                self.result("finalizing", token, outcome)
                return "written"
            except InvalidTerminalTransition:
                return "rejected"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(publish, ["complete", "failed"]))

        self.assertEqual(outcomes.count("written"), 1)
        self.assertEqual(outcomes.count("rejected"), 1)
        self.assertIn(
            read_terminal_state(self.store, self.config, self.job_id)["state"],
            {"complete", "failed"},
        )

    def test_concurrent_identical_results_are_idempotent(self) -> None:
        token = self.claim("finalizing")
        barrier = threading.Barrier(8)

        def publish(_: int) -> None:
            barrier.wait()
            self.result("finalizing", token, "complete")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(publish, range(8)))

        self.assertEqual(
            read_terminal_state(self.store, self.config, self.job_id)["state"],
            "complete",
        )

    def test_finalization_error_cannot_become_complete(self) -> None:
        token = self.claim("finalizing")
        self.result("finalizing", token, "failed")

        with self.assertRaises(InvalidTerminalTransition):
            self.result("finalizing", token, "complete")

    def test_cancelled_cannot_become_cancellation_failed(self) -> None:
        token = self.claim("cancelled")
        self.result("cancelled", token, "cancelled")

        with self.assertRaises(InvalidTerminalTransition):
            self.result("cancelled", token, "cancellation_failed")

    def test_malformed_unhashable_claim_is_rejected(self) -> None:
        self.claim("finalizing")
        key = terminal_claim_key(self.config.run_id, self.job_id)
        record = self.store.get(key)
        record["claim"] = ["finalizing"]
        self.store.overwrite_for_test(key, record)

        with self.assertRaises(GuardUnavailable):
            read_terminal_state(self.store, self.config, self.job_id)

    def test_malformed_outcome_identity_is_rejected(self) -> None:
        token = self.claim("finalizing")
        self.result("finalizing", token, "complete")
        key = terminal_result_key(self.config.run_id, self.job_id)
        record = self.store.get(key)
        record["job_id"] = str(uuid.uuid4())
        self.store.overwrite_for_test(key, record)

        with self.assertRaises(GuardUnavailable):
            read_terminal_state(self.store, self.config, self.job_id)

    def test_malformed_outcome_owner_is_rejected(self) -> None:
        token = self.claim("finalizing")
        self.result("finalizing", token, "complete")
        key = terminal_result_key(self.config.run_id, self.job_id)
        record = self.store.get(key)
        record["owner_claim"] = "cancelled"
        self.store.overwrite_for_test(key, record)

        with self.assertRaises(GuardUnavailable):
            read_terminal_state(self.store, self.config, self.job_id)

    def test_malformed_unhashable_outcome_is_rejected(self) -> None:
        token = self.claim("finalizing")
        self.result("finalizing", token, "complete")
        key = terminal_result_key(self.config.run_id, self.job_id)
        record = self.store.get(key)
        record["result"] = ["complete"]
        self.store.overwrite_for_test(key, record)

        with self.assertRaises(GuardUnavailable):
            read_terminal_state(self.store, self.config, self.job_id)

    def test_missing_sentinel_blocks_result_recording(self) -> None:
        token = self.claim("finalizing")
        self.store.remove_for_test(RUN_CONFIG_KEY)

        with self.assertRaises(InvalidRunConfig):
            self.result("finalizing", token, "complete")

    def test_store_write_failure_fails_closed(self) -> None:
        self.store.fail_put = True
        with self.assertRaises(GuardUnavailable):
            self.claim("cancelled")


class CleanupTests(GuardFixture):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="sonora-guard-cleanup-")
        super().setUp()
        self.admit()

        self.root = Path(self.temp_dir.name)
        self.job_dir = self.root / "jobs" / self.job_id
        self.library_dir = self.root / "library"
        self.job_dir.mkdir(parents=True)
        self.library_dir.mkdir(parents=True)

        self.audio_file = self.job_dir / "track.mp3"
        self.audio_file.write_bytes(b"fake-audio")
        self.metadata_file = self.library_dir / f"{self.job_id}.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def add_completed_metadata(self) -> None:
        self.metadata_file.write_text(
            json.dumps(
                {
                    "job_id": self.job_id,
                    "status": "complete",
                    "file": f"jobs/{self.job_id}/track.mp3",
                    "filename": "track.mp3",
                }
            ),
            encoding="utf-8",
        )

    def cleanup(self, token: str, *, rollback: bool = False):
        return cleanup_job_files(
            self.root,
            self.job_id,
            guard_store=self.store,
            run_config=self.config,
            claim_token=token,
            clock=self.clock,
            finalization_rollback=rollback,
        )

    def test_failed_cleanup_removes_only_its_own_job_directory(self) -> None:
        token = self.claim("failed")
        self.result("failed", token, "failed")

        sibling_id = str(uuid.uuid4())
        sibling = self.root / "jobs" / sibling_id
        sibling.mkdir()
        (sibling / "keep.mp3").write_bytes(b"keep")

        removed = self.cleanup(token)

        self.assertIn(f"jobs/{self.job_id}", removed)
        self.assertFalse(self.job_dir.exists())
        self.assertTrue((sibling / "keep.mp3").exists())

    def test_cancelled_cleanup_removes_its_own_job_directory(self) -> None:
        token = self.claim("cancelled")
        self.result("cancelled", token, "cancelled")

        self.cleanup(token)
        self.assertFalse(self.job_dir.exists())

    def test_cleanup_preserves_completed_library_record_and_audio(self) -> None:
        self.add_completed_metadata()
        token = self.claim("failed")
        self.result("failed", token, "failed")

        with self.assertRaises(LibraryRecordProtected):
            self.cleanup(token)

        self.assertTrue(self.metadata_file.exists())
        self.assertTrue(self.audio_file.exists())

    def test_unconfirmed_cancellation_cannot_clean_files(self) -> None:
        token = self.claim("cancelled")
        self.result("cancelled", token, "cancellation_failed")

        with self.assertRaises(InvalidTerminalTransition):
            self.cleanup(token)

        self.assertTrue(self.audio_file.exists())

    def test_finalization_rollback_requires_explicit_flag(self) -> None:
        self.add_completed_metadata()
        token = self.claim("finalizing")

        with self.assertRaises(InvalidTerminalTransition):
            self.cleanup(token)

        self.assertTrue(self.metadata_file.exists())
        self.assertTrue(self.audio_file.exists())

    def test_finalization_rollback_claims_failed_before_deleting(self) -> None:
        self.add_completed_metadata()
        token = self.claim("finalizing")

        removed = self.cleanup(token, rollback=True)

        state = read_terminal_state(self.store, self.config, self.job_id)
        self.assertEqual(state["state"], "failed")
        self.assertIn(f"library/{self.job_id}.json", removed)
        self.assertIn(f"jobs/{self.job_id}", removed)
        self.assertFalse(self.metadata_file.exists())
        self.assertFalse(self.job_dir.exists())

    def test_complete_outcome_wins_against_rollback(self) -> None:
        self.add_completed_metadata()
        token = self.claim("finalizing")
        self.result("finalizing", token, "complete")

        with self.assertRaises(InvalidTerminalTransition):
            self.cleanup(token, rollback=True)

        self.assertTrue(self.metadata_file.exists())
        self.assertTrue(self.audio_file.exists())

    def test_wrong_token_cannot_clean(self) -> None:
        token = self.claim("failed")
        self.result("failed", token, "failed")

        with self.assertRaises(InvalidTerminalTransition):
            self.cleanup(uuid.uuid4().hex)

        self.assertTrue(self.audio_file.exists())

    def test_symlinked_job_directory_is_refused(self) -> None:
        token = self.claim("failed")
        self.result("failed", token, "failed")

        outside = Path(self.temp_dir.name).parent / f"sonora-guard-outside-{uuid.uuid4()}"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")

        try:
            shutil.rmtree(self.job_dir)
            self.job_dir.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(UnsafeCleanupPath):
                self.cleanup(token)

            self.assertTrue((outside / "keep.txt").exists())
        finally:
            if outside.exists():
                shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
