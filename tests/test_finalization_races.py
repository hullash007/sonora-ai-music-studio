"""Real Python threads over atomic fakes, never platform concurrency claims."""
import concurrent.futures
import threading
import unittest
import test_worker as fixtures
from sonora.guard import (claim_terminal, record_terminal_result,
                          cleanup_job_files, read_terminal_state)


class FinalizationRaceTests(unittest.TestCase):
    def race(self, left, right):
        barrier = threading.Barrier(2)
        def run(callback):
            barrier.wait(timeout=5)
            try:
                return callback()
            except Exception:
                return None
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            return list(pool.map(run, [left, right]))

    def test_finalizing_cancelled_true_concurrent_100_interleavings(self):
        for iteration in range(100):
            case = fixtures.WorkerFixture(); case.setUp()
            try:
                def claim(owner):
                    token = claim_terminal(case.guard, case.run_config, case.job_id, owner)
                    return (owner, token) if token else None
                winners = [r for r in self.race(lambda: claim('finalizing'),
                                               lambda: claim('cancelled')) if r]
                self.assertEqual(len(winners), 1)
                owner, token = winners[0]
                loser = 'cancelled' if owner == 'finalizing' else 'finalizing'
                with self.assertRaises(Exception):
                    record_terminal_result(case.guard, case.run_config, case.job_id,
                                           owner_claim=loser, claim_token=token,
                                           result='cancelled' if loser == 'cancelled' else 'complete')
                state = read_terminal_state(case.guard, case.run_config, case.job_id)
                self.assertEqual(state['claim'], owner)
            finally:
                case.tearDown()

    def test_complete_and_disabled_rollback_race_preserves_audio_100_times(self):
        for iteration in range(100):
            case = fixtures.WorkerFixture(); case.setUp()
            try:
                audio = case.create_fake_audio()
                token = claim_terminal(case.guard, case.run_config, case.job_id, 'finalizing')
                def complete():
                    record_terminal_result(case.guard, case.run_config, case.job_id,
                                           owner_claim='finalizing', claim_token=token, result='complete')
                    return 'complete'
                def rollback():
                    cleanup_job_files(case.audio_root, case.job_id, guard_store=case.guard,
                                      run_config=case.run_config, claim_token=token,
                                      finalization_rollback=True)
                    return 'failed'
                self.race(complete, rollback)
                outcome = read_terminal_state(case.guard, case.run_config, case.job_id)['state']
                self.assertEqual(outcome, 'complete')
                self.assertEqual(audio.exists(), outcome == 'complete')
                record_terminal_result(case.guard, case.run_config, case.job_id,
                                       owner_claim='finalizing', claim_token=token, result=outcome)
                with self.assertRaises(Exception):
                    record_terminal_result(case.guard, case.run_config, case.job_id,
                                           owner_claim='finalizing', claim_token=token,
                                           result='failed' if outcome == 'complete' else 'complete')
            finally:
                case.tearDown()
