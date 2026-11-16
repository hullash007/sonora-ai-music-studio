import unittest
import test_worker as fixtures
from sonora.guard import read_terminal_state
from sonora.recovery import read_receipt


class CommitAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.WorkerFixture(); self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def test_commit_success_with_lost_callback_ack_never_rolls_back_or_recommits(self):
        c = self.case; calls = []
        def commit():
            calls.append('remote success before lost ack')
            raise TimeoutError('ack lost')
        result = c.run_worker(c.create_fake_audio, commit_audio=commit)
        self.assertEqual(result['status'], 'reconciliation_required')
        self.assertEqual(len(calls), 1)
        self.assertTrue(c.job_dir.exists())
        self.assertEqual(read_terminal_state(c.guard, c.run_config, c.job_id)['state'], 'finalizing')
        self.assertIsNone(read_receipt(c.guard, c.run_config, c.job_id, 'audio-commit-completed'))

    def test_commit_receipt_lost_ack_is_read_back_and_completes(self):
        c = self.case; original = c.guard.put
        def lost(key, value, *, skip_if_exists=False):
            result = original(key, value, skip_if_exists=skip_if_exists)
            if key.endswith(':audio-commit-completed'):
                raise TimeoutError('receipt response lost')
            return result
        c.guard.put = lost
        self.assertEqual(c.run_worker(c.create_fake_audio)['status'], 'complete')
        self.assertEqual(c.commit_calls, 1)

    def test_missing_completion_receipt_preserves_finalization_and_audio(self):
        c = self.case; original = c.guard.put
        def unavailable(key, value, *, skip_if_exists=False):
            if key.endswith(':audio-commit-completed'):
                raise TimeoutError('receipt unavailable')
            return original(key, value, skip_if_exists=skip_if_exists)
        c.guard.put = unavailable
        self.assertEqual(c.run_worker(c.create_fake_audio)['status'], 'reconciliation_required')
        self.assertEqual(c.commit_calls, 1)
        self.assertTrue(c.job_dir.exists())
        self.assertEqual(read_terminal_state(c.guard, c.run_config, c.job_id)['state'], 'finalizing')

    def test_commit_permission_lost_ack_never_invokes_callback(self):
        c = self.case; original = c.guard.put
        def lost(key, value, *, skip_if_exists=False):
            result = original(key, value, skip_if_exists=skip_if_exists)
            if key.endswith(':audio-commit-started'):
                raise TimeoutError('permission ack lost')
            return result
        c.guard.put = lost
        self.assertEqual(c.run_worker(c.create_fake_audio)['status'], 'audio_commit_unknown')
        self.assertEqual(c.commit_calls, 0)
        self.assertTrue(c.job_dir.exists())
