import unittest
import test_api as fixtures


class CleanupReceiptRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.GuardedSonoraApiTests(); self.case.setUp()
        self.job = self.case.submit().json()['job_id']

    def tearDown(self):
        self.case.tearDown()

    def status(self):
        return self.case.client.get('/api/status/' + self.job, headers=fixtures.AUTH_HEADERS).json()

    def interrupt(self, suffix, after_write):
        c = self.case; original = c.guard_store.put
        def broken(key, value, *, skip_if_exists=False):
            if key.endswith(suffix):
                if after_write:
                    original(key, value, skip_if_exists=skip_if_exists)
                raise TimeoutError('receipt lost')
            return original(key, value, skip_if_exists=skip_if_exists)
        c.guard_store.put = broken
        return original

    def prepare(self):
        self.case.client.post('/api/cancel/' + self.job, headers=fixtures.AUTH_HEADERS)

    def test_cleanup_success_lost_receipt_is_unknown_after_restart_never_repeated(self):
        self.prepare(); original = self.interrupt(':cleanup-completed', False)
        first = self.status()
        self.case.guard_store.put = original
        self.assertEqual(first['status'], 'cancelled')
        self.assertEqual(first['cleanup_state'], 'cleanup_outcome_unknown')
        self.assertEqual(self.status()['cleanup_state'], 'cleanup_outcome_unknown')
        self.assertEqual(len(self.case.cleanup_calls), 1)

    def test_cleanup_receipt_ack_loss_exact_readback_confirms_success(self):
        self.prepare(); original = self.interrupt(':cleanup-completed', True)
        self.assertEqual(self.status()['cleanup_state'], 'cleanup_completed')
        self.case.guard_store.put = original
        self.assertEqual(self.status()['cleanup_state'], 'cleanup_completed')
        self.assertEqual(len(self.case.cleanup_calls), 1)

    def test_cleanup_permission_ack_loss_never_runs_callback(self):
        self.prepare(); original = self.interrupt(':cancel-cleanup', True)
        self.status(); self.case.guard_store.put = original
        self.assertEqual(self.status()['cleanup_state'], 'cleanup_outcome_unknown')
        self.assertEqual(len(self.case.cleanup_calls), 0)

    def test_partial_cleanup_failure_stays_unknown_not_completed(self):
        self.case.cleanup_error = OSError('partial callback failed')
        self.prepare()
        self.assertEqual(self.status()['cleanup_state'], 'cleanup_outcome_unknown')
        self.assertEqual(self.status()['cleanup_state'], 'cleanup_outcome_unknown')
        self.assertEqual(len(self.case.cleanup_calls), 1)
