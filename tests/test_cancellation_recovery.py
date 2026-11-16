"""Cancellation boundary tests, fake adapters only."""
import unittest
import test_api as api
from sonora.guard import read_terminal_state


class CancellationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = api.GuardedSonoraApiTests()
        self.case.setUp()
        self.case.termination_confirmed = False
        self.job = self.case.submit().json()['job_id']

    def tearDown(self):
        self.case.tearDown()

    def cancel(self):
        return self.case.client.post('/api/cancel/' + self.job, headers=api.AUTH_HEADERS)

    def status(self):
        return self.case.client.get('/api/status/' + self.job, headers=api.AUTH_HEADERS)

    def interrupt(self, suffix, after_write):
        c = self.case
        original = c.guard_store.put
        def interrupted(key, value, *, skip_if_exists=False):
            if key.endswith(suffix):
                if after_write:
                    original(key, value, skip_if_exists=skip_if_exists)
                raise TimeoutError('simulated boundary interruption')
            return original(key, value, skip_if_exists=skip_if_exists)
        c.guard_store.put = interrupted
        return original

    def test_dispatch_claim_lost_ack_never_grants_cancel_permission(self):
        original = self.interrupt(':cancel-dispatch', True)
        response = self.cancel()
        self.assertEqual(response.json()['status'], 'cancel_dispatch_unknown')
        self.case.guard_store.put = original
        self.assertEqual(self.case.cancel_calls, [])
        self.assertEqual(self.status().json()['status'], 'cancel_dispatch_unknown')
        self.assertEqual(self.case.cleanup_calls, [])
        self.cancel()
        self.assertEqual(self.case.cancel_calls, [])

    def test_lost_acceptance_receipt_recovers_by_terminal_poll_without_redispatch(self):
        original = self.interrupt(':cancel-dispatch-result', False)
        self.assertEqual(self.cancel().status_code, 503)
        self.case.guard_store.put = original
        self.assertEqual(len(self.case.cancel_calls), 1)
        self.assertEqual(self.status().json()['status'], 'cancel_dispatch_unknown')
        self.assertEqual(self.case.cleanup_calls, [])
        self.case.termination_confirmed = True
        self.assertEqual(self.status().json()['status'], 'cancelled')
        self.assertEqual(len(self.case.cancel_calls), 1)
        self.assertEqual(len(self.case.cleanup_calls), 1)
        self.assertEqual(self.status().json()['status'], 'cancelled')
        self.assertEqual(len(self.case.cleanup_calls), 1)

    def test_generic_cancel_exception_is_unknown_not_failed(self):
        def ambiguous(*args):
            raise TimeoutError('acceptance response lost')
        # The app closes over its cancel callable, so replace via a fresh app.
        from fastapi.testclient import TestClient
        with TestClient(self.case.make_app(cancel=ambiguous)) as client:
            response = client.post('/api/cancel/' + self.job, headers=api.AUTH_HEADERS)
            self.assertEqual(response.json()['status'], 'cancel_dispatch_unknown')
        self.assertEqual(read_terminal_state(self.case.guard_store, self.case.run_config, self.job)['state'], 'cancelling')
        self.assertEqual(self.case.cleanup_calls, [])

    def test_request_store_failure_never_calls_cancel(self):
        original = self.interrupt(':cancel-request', False)
        self.assertEqual(self.cancel().status_code, 503)
        self.case.guard_store.put = original
        self.assertEqual(self.case.cancel_calls, [])
        self.assertEqual(self.case.cleanup_calls, [])

    def test_terminal_observation_error_never_cleans_or_redispatches(self):
        self.cancel()
        from fastapi.testclient import TestClient
        from sonora.web import create_app
        c = self.case
        def unavailable(call):
            raise TimeoutError('statistics unavailable')
        app = create_app(job_store=c.job_store, enqueue_generation=c.enqueue,
                         audio_root=c.audio_root, reload_audio=lambda: None,
                         password=api.TEST_PASSWORD, guard_store=c.guard_store,
                         run_config_reader=lambda: c.run_config,
                         cancel_generation=c.cancel, require_guard=True,
                         generation_termination_confirmed=unavailable,
                         cleanup_cancelled_generation=c.cleanup)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/status/' + self.job, headers=api.AUTH_HEADERS).json()['status'], 'cancellation_accepted')
        self.assertEqual(c.cleanup_calls, [])
        self.assertEqual(len(c.cancel_calls), 1)

    def test_dispatch_store_failure_never_invokes_cancel(self):
        original = self.interrupt(':cancel-dispatch', False)
        self.assertEqual(self.cancel().status_code, 503)
        self.case.guard_store.put = original
        self.assertEqual(self.case.cancel_calls, [])

    def test_confirmed_stop_without_acceptance_does_not_fabricate_acceptance(self):
        original = self.interrupt(':cancel-dispatch', True)
        self.cancel()
        self.case.guard_store.put = original
        self.case.termination_confirmed = True
        response = self.status().json()
        self.assertEqual(response['status'], 'cancelled')
        self.assertTrue(response['termination_confirmed'])
        self.assertFalse(response['cancellation_request_accepted'])
        self.assertEqual(self.case.cancel_calls, [])
        self.assertEqual(len(self.case.cleanup_calls), 1)
