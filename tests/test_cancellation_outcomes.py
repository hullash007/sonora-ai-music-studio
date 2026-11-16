import unittest
import test_api as fixtures
from sonora.guard import read_terminal_state


class CancellationOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.c = fixtures.GuardedSonoraApiTests(); self.c.setUp()
        self.job = self.c.submit().json()['job_id']

    def tearDown(self):
        self.c.tearDown()

    def status(self):
        return self.c.client.get('/api/status/' + self.job, headers=fixtures.AUTH_HEADERS).json()

    def cancel(self):
        return self.c.client.post('/api/cancel/' + self.job, headers=fixtures.AUTH_HEADERS)

    def test_crash_before_cancel_then_normal_return_never_cancels_or_cleans(self):
        original = self.c.guard_store.put
        def lost(key, value, *, skip_if_exists=False):
            result = original(key, value, skip_if_exists=skip_if_exists)
            if key.endswith(':cancel-dispatch'):
                raise TimeoutError('permission ack lost before cancel call')
            return result
        self.c.guard_store.put = lost
        self.cancel(); self.c.guard_store.put = original
        self.c.termination_reason = 'returned'
        self.assertEqual(self.status()['status'], 'reconciliation_required')
        self.assertEqual(read_terminal_state(self.c.guard_store, self.c.run_config, self.job)['state'], 'cancelling')
        self.assertEqual(self.c.cancel_calls, [])
        self.assertEqual(self.c.cleanup_calls, [])

    def test_accepted_cancel_plus_natural_return_or_timeout_is_not_cancellation(self):
        self.cancel()
        for reason in ['returned', 'execution_timed_out']:
            # Fresh job per terminal reason avoids changing immutable evidence.
            if reason == 'execution_timed_out':
                self.c.tearDown(); self.c = fixtures.GuardedSonoraApiTests(); self.c.setUp()
                self.job = self.c.submit().json()['job_id']; self.cancel()
            self.c.termination_reason = reason
            self.assertEqual(self.status()['status'], 'reconciliation_required')
            self.assertEqual(self.c.cleanup_calls, [])

    def test_unknown_terminal_reason_never_cleans(self):
        self.cancel(); self.c.termination_reason = 'unknown'
        self.assertEqual(self.status()['status'], 'cancellation_accepted')
        self.assertEqual(self.c.cleanup_calls, [])

    def test_explicit_cleanup_failure_return_is_not_success(self):
        from fastapi.testclient import TestClient
        from sonora.web import create_app
        self.cancel()
        app = create_app(job_store=self.c.job_store, enqueue_generation=self.c.enqueue,
                         audio_root=self.c.audio_root, reload_audio=lambda:None,
                         password=fixtures.TEST_PASSWORD, guard_store=self.c.guard_store,
                         run_config_reader=lambda:self.c.run_config,
                         cancel_generation=self.c.cancel, require_guard=True,
                         generation_termination_confirmed=lambda call:{'call_id':call,'state':'input_cancelled','termination_confirmed':True,'zero_activity':True},
                         cleanup_cancelled_generation=lambda *a:False)
        with TestClient(app) as client:
            response = client.get('/api/status/'+self.job, headers=fixtures.AUTH_HEADERS).json()
            self.assertEqual(response['cleanup_state'], 'cleanup_outcome_unknown')
            self.assertIn('cleanup failed', response['error'])
        self.assertFalse(any(k.endswith(':cleanup-completed') for k in self.c.guard_store.data))

    def test_definitive_rejection_is_not_overwritten_by_later_cancel_observation(self):
        self.c.cancel_result = False
        self.assertEqual(self.cancel().json()['status'], 'cancellation_failed')
        self.c.termination_reason = 'input_cancelled'
        self.assertEqual(self.status()['status'], 'cancellation_failed')
        self.assertEqual(self.c.cleanup_calls, [])

    def test_wrong_call_or_legacy_boolean_never_authorizes_cancellation_cleanup(self):
        from fastapi.testclient import TestClient
        from sonora.web import create_app
        self.cancel()
        for evidence in [True, {'call_id':'other','state':'input_cancelled','termination_confirmed':True,'zero_activity':True}]:
            app = create_app(job_store=self.c.job_store, enqueue_generation=self.c.enqueue,
                         audio_root=self.c.audio_root, reload_audio=lambda:None,
                         password=fixtures.TEST_PASSWORD, guard_store=self.c.guard_store,
                         run_config_reader=lambda:self.c.run_config,
                         cancel_generation=self.c.cancel, require_guard=True,
                         generation_termination_confirmed=lambda call:evidence,
                         cleanup_cancelled_generation=self.c.cleanup)
            with TestClient(app) as client:
                response = client.get('/api/status/'+self.job, headers=fixtures.AUTH_HEADERS).json()
                self.assertEqual(response['status'], 'cancellation_accepted')
        self.assertEqual(self.c.cleanup_calls, [])

    def test_call_identity_substitution_after_dispatch_cannot_finalize(self):
        self.c.termination_confirmed = False
        self.cancel()
        key = next(k for k in self.c.guard_store.data if k.endswith(':function-call'))
        # Adversarial fixture only; real immutable receipts cannot be updated.
        self.c.guard_store.data[key] = {**self.c.guard_store.data[key], 'call_id':'other-call'}
        self.c.termination_confirmed = True
        self.assertEqual(self.status()['status'], 'cancellation_accepted')
        self.assertEqual(self.c.cleanup_calls, [])
        self.assertEqual(read_terminal_state(self.c.guard_store, self.c.run_config, self.job)['state'], 'cancelling')
