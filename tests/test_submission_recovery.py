"""All fake stores/calls. No Modal, model, network or audio generation."""
import unittest
import test_api as api
from sonora.guard import admission_key, read_terminal_state
from sonora.recovery import (DefinitiveSubmissionRejected, append_receipt,
                             read_receipt, request_fingerprint)
from fastapi.testclient import TestClient


class SubmissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = api.GuardedSonoraApiTests()
        self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def state(self, job):
        return self.case.client.get('/api/status/' + job, headers=api.AUTH_HEADERS)

    def test_job_cache_failure_consumes_admission_without_dispatch(self):
        class BrokenCache(dict):
            def __setitem__(self, key, value):
                raise OSError('cache unavailable')
        c = self.case
        c.job_store = BrokenCache()
        with TestClient(c.make_app()) as client:
            response = c.submit(client)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()['status'], 'pre_dispatch_failed')
            job = response.json()['job_id']
            self.assertEqual(client.get('/api/status/' + job, headers=api.AUTH_HEADERS).json()['status'], 'pre_dispatch_failed')
            self.assertEqual(c.submit(client).status_code, 409)
        self.assertEqual(c.enqueue_calls, [])

    def test_ambiguous_spawn_never_claims_worker_failed_or_retries(self):
        c = self.case
        calls = []
        def lost_response(*args):
            calls.append(args)
            raise TimeoutError('accepted remotely, reply lost')
        with TestClient(c.make_app(enqueue=lost_response)) as client:
            response = c.submit(client)
            self.assertEqual(response.json()['status'], 'submission_unknown')
            job = response.json()['job_id']
            self.assertEqual(client.get('/api/status/' + job, headers=api.AUTH_HEADERS).json()['status'], 'submission_unknown')
            self.assertIsNone(read_terminal_state(c.guard_store, c.run_config, job))
            self.assertEqual(c.submit(client).status_code, 409)
        self.assertEqual(len(calls), 1)
        self.assertEqual(c.cleanup_calls, [])

    def test_lost_dispatch_ack_grants_no_spawn_permission(self):
        c = self.case
        original = c.guard_store.put
        def lost_ack(key, value, *, skip_if_exists=False):
            result = original(key, value, skip_if_exists=skip_if_exists)
            if key.endswith(':dispatch-started'):
                raise TimeoutError('write succeeded, response lost')
            return result
        c.guard_store.put = lost_ack
        response = c.submit()
        self.assertEqual(response.json()['status'], 'submission_unknown')
        self.assertEqual(c.enqueue_calls, [])
        self.assertEqual(self.state(response.json()['job_id']).json()['status'], 'submission_unknown')

    def test_dispatch_store_failure_never_calls_spawn(self):
        c = self.case
        original = c.guard_store.put
        def unavailable(key, value, *, skip_if_exists=False):
            if key.endswith(':dispatch-started'):
                raise TimeoutError('write unavailable')
            return original(key, value, skip_if_exists=skip_if_exists)
        c.guard_store.put = unavailable
        self.assertEqual(c.submit().status_code, 503)
        self.assertEqual(c.enqueue_calls, [])

    def test_call_receipt_lost_ack_is_read_back_without_second_spawn(self):
        c = self.case
        original = c.guard_store.put
        def lost_ack(key, value, *, skip_if_exists=False):
            result = original(key, value, skip_if_exists=skip_if_exists)
            if key.endswith(':function-call'):
                raise TimeoutError('receipt response lost')
            return result
        c.guard_store.put = lost_ack
        response = c.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(c.enqueue_calls), 1)
        job = response.json()['job_id']
        self.assertEqual(read_receipt(c.guard_store, c.run_config, job, 'function-call')['call_id'], 'fake-call-' + job)

    def test_call_receipt_failure_retains_hint_and_no_terminal_claim(self):
        c = self.case
        original = c.guard_store.put
        def unavailable(key, value, *, skip_if_exists=False):
            if key.endswith(':function-call'):
                raise TimeoutError('receipt unavailable')
            return original(key, value, skip_if_exists=skip_if_exists)
        c.guard_store.put = unavailable
        response = c.submit()
        job = response.json()['job_id']
        self.assertEqual(response.json()['status'], 'reconciliation_required')
        self.assertEqual(c.job_store[job]['_call_id_hint'], 'fake-call-' + job)
        self.assertEqual(len(c.enqueue_calls), 1)
        self.assertIsNone(read_terminal_state(c.guard_store, c.run_config, job))
        c.guard_store.put = original
        append_receipt(c.guard_store, c.run_config, job, 'function-call', call_id='fake-call-' + job)
        self.assertEqual(self.state(job).json()['status'], 'queued')
        self.assertEqual(len(c.enqueue_calls), 1)

    def test_definitive_rejection_is_explicit_not_ambiguous(self):
        def reject(*args):
            raise DefinitiveSubmissionRejected('proven no call created')
        c = self.case
        with TestClient(c.make_app(enqueue=reject)) as client:
            response = c.submit(client)
            self.assertEqual(response.json()['status'], 'submission_rejected')
            self.assertEqual(client.get('/api/status/' + response.json()['job_id'], headers=api.AUTH_HEADERS).json()['status'], 'submission_rejected')

    def test_fingerprint_has_no_raw_creative_text_and_cache_cannot_complete(self):
        c = self.case
        job = c.submit().json()['job_id']
        intent = c.guard_store.data[admission_key(c.run_config.run_id)]
        self.assertEqual(intent['protocol_version'], 2)
        self.assertEqual(intent['request_sha256'], request_fingerprint('A short test prompt', '', 30))
        self.assertNotIn('prompt', intent)
        self.assertNotIn('lyrics', intent)
        c.job_store[job]['status'] = 'complete'
        self.assertEqual(self.state(job).json()['status'], 'reconciliation_required')

    def test_immutable_receipt_replay_never_grants_launch_permission(self):
        c = self.case
        job = c.submit().json()['job_id']
        self.assertFalse(append_receipt(c.guard_store, c.run_config, job, 'dispatch-started'))
        self.assertEqual(len(c.enqueue_calls), 1)
        with self.assertRaises(Exception):
            append_receipt(c.guard_store, c.run_config, job, 'function-call', call_id='conflicting-call')
        self.assertEqual(read_receipt(c.guard_store, c.run_config, job, 'function-call')['call_id'], 'fake-call-' + job)

    def test_missing_job_cache_is_reconstructed_from_admission_without_spawn(self):
        c = self.case
        job = c.submit().json()['job_id']
        c.job_store.clear()
        response = self.state(job)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'submitted')
        self.assertEqual(len(c.enqueue_calls), 1)

    def test_unavailable_job_cache_does_not_hide_durable_submission(self):
        c = self.case
        job = c.submit().json()['job_id']
        class Unavailable(dict):
            def get(self, *args, **kwargs):
                raise TimeoutError('cache unavailable')
        c.job_store = Unavailable()
        with TestClient(c.make_app()) as client:
            response = client.get('/api/status/' + job, headers=api.AUTH_HEADERS)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'submitted')
        self.assertEqual(len(c.enqueue_calls), 1)
