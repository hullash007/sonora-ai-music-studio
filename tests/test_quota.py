import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sonora.guard import claim_terminal, record_terminal_result
from sonora.shared import SharedAdmission, ScopedGuard, CLOSES_AT, RESERVATION
from sonora.quota import VisitorQuota, POLICY_KEY, LIMIT, account_key
from test_shared import Store


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.now = CLOSES_AT - 86400
        self.store.put(POLICY_KEY, {'limit': 3, 'ready': True, 'rollout_at': self.now})
        self.identity = {'sub': 'new-visitor', 'role': 'visitor'}
        self.states = {}
        self.idle = True
        self.quota = VisitorQuota(self.store, lambda: self.idle, lambda c: self.states.get(c, 'unknown'), lambda: self.now)
        self.guard = ScopedGuard(self.store)
        self.manager = self.new_manager()
        self.configs = {}

    def new_manager(self):
        return SharedAdmission(self.store, self.guard, lambda: self.idle, lambda c: self.states.get(c, 'unknown'),
                               lambda: self.now, quota=self.quota, identity_reader=lambda: self.identity)

    def admit(self):
        job = str(uuid.uuid4())
        config = self.manager.admit(job, 'a' * 64)
        self.configs[job] = config
        self.store.put('shared-call:' + job, 'call-' + job)
        return job

    def settle(self, job, result='complete', remote='returned'):
        config = self.configs[job]
        self.guard.select(config.run_id)
        claim = 'finalizing' if result == 'complete' else result
        token = claim_terminal(self.guard, config, job, claim, clock=lambda: self.now)
        record_terminal_result(self.guard, config, job, owner_claim=claim, claim_token=token,
                               result=result, clock=lambda: self.now)
        self.manager.finish(job, 1)
        self.states['call-' + job] = remote

    def test_three_successes_then_fourth_blocked_without_credit_or_worker(self):
        for expected in range(1, 4):
            job = self.admit()
            self.settle(job)
            self.assertEqual(self.quota.status(self.identity)['completed'], expected)
        self.manager = self.new_manager()  # Reconstructed process, same durable store.
        credits = self.manager.spent()
        with self.assertRaises(HTTPException) as caught:
            self.admit()
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(self.manager.spent(), credits)
        self.assertIsNone(self.store.get('shared-busy'))

    def test_pending_is_reserved_and_ambiguous_failure_not_refunded(self):
        job = self.admit()
        self.assertEqual(self.quota.status(self.identity)['pending'], 1)
        self.settle(job, 'failed', remote='unknown')
        self.assertEqual(self.quota.status(self.identity)['pending'], 1)
        self.states['call-' + job] = 'returned'
        self.idle = False
        self.assertEqual(self.quota.status(self.identity)['pending'], 1)
        self.idle = True
        self.assertEqual(self.quota.status(self.identity)['pending'], 0)
        self.assertEqual(self.quota.status(self.identity)['remaining'], 3)
        self.assertGreater(self.manager.spent(), 0)  # Quota refund never refunds compute budget.

    def test_confirmed_failed_and_cancelled_do_not_use_success_allowance(self):
        for result in ['failed', 'cancelled']:
            job = self.admit()
            self.settle(job, result)
            self.assertEqual(self.quota.status(self.identity)['remaining'], 3)
        for _ in range(3):
            self.settle(self.admit())
        self.assertEqual(self.quota.status(self.identity)['completed'], 3)

    def test_accounts_are_isolated_and_email_does_not_reset_subject(self):
        for _ in range(3):
            self.settle(self.admit())
        self.identity = {'sub': 'another-google-subject', 'role': 'visitor'}
        self.assertEqual(self.quota.status(self.identity)['remaining'], 3)
        self.identity = {'sub': 'new-visitor', 'role': 'visitor', 'email': 'changed@example.com'}
        self.assertEqual(self.quota.status(self.identity)['remaining'], 0)

    def test_owner_and_seeded_existing_account_are_not_limited(self):
        self.identity = {'sub': 'owner-subject', 'role': 'owner'}
        self.assertEqual(self.quota.status(self.identity), {'limited': False})
        self.identity = {'sub': 'existing-visitor', 'role': 'visitor'}
        self.store.put(account_key(self.identity['sub']), {'kind': 'visitor-quota-account-v1', 'exempt': True, 'registered_at': self.now - 1})
        for _ in range(4):
            self.settle(self.admit())
        self.assertFalse(self.quota.status(self.identity)['limited'])

    def test_concurrent_requests_at_last_slot_admit_only_one(self):
        for _ in range(2):
            self.settle(self.admit())
        barrier = Barrier(2)
        def submit(_):
            barrier.wait()
            try:
                return self.admit()
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(isinstance(v, str) for v in results), 1)
        self.assertEqual(sum(v == 409 for v in results), 1)
        self.assertEqual(self.quota.status(self.identity)['remaining'], 0)

    def test_policy_or_identity_missing_fails_closed(self):
        self.store.data.pop(POLICY_KEY)
        with self.assertRaises(HTTPException):
            self.admit()
        self.assertFalse(any(k.startswith('credit-reservation:') for k in self.store.keys()))
        self.store.put(POLICY_KEY, {'limit': LIMIT, 'ready': True})
        self.identity = None
        with self.assertRaises(HTTPException):
            self.quota.status(self.identity)

    def test_reservation_write_fault_blocks_new_dispatch(self):
        original = self.store.put
        def fail(key, *args, **kwargs):
            if ':job:' in key and key.startswith('visitor-quota-v1:'):
                raise RuntimeError('synthetic durable-store error')
            return original(key, *args, **kwargs)
        self.store.put = fail
        with self.assertRaises(RuntimeError):
            self.admit()
        self.store.put = original
        with self.assertRaises(HTTPException) as caught:
            self.admit()
        self.assertEqual(caught.exception.status_code, 409)

    def test_credit_cutoff_still_wins(self):
        self.now = CLOSES_AT
        with self.assertRaises(HTTPException) as caught:
            self.admit()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertFalse(any(':job:' in k for k in self.store.keys()))


class QuotaGoogleApiTests(QuotaTests):
    def test_authenticated_api_fourth_call_rejected_and_client_fields_cannot_bypass(self):
        from sonora.web import create_app
        from sonora.google_auth import install_google_auth, current_identity, principal
        origin = 'https://studio.example'
        claims = {}
        calls = []
        with tempfile.TemporaryDirectory() as root:
            manager = SharedAdmission(self.store, self.guard, lambda: True, lambda _: 'returned', lambda: self.now,
                                      quota=self.quota, identity_reader=current_identity.get)
            def enqueue(job, *_):
                config = manager.lookup(self.store.get('shared-busy') and self.configs[job].run_id)
                calls.append(job)
                self.store.put('shared-call:' + job, 'call-' + job)
                self.settle(job)
                return 'call-' + job
            def admit(job, fingerprint):
                config = manager.admit(job, fingerprint)
                self.configs[job] = config
                return config
            app = create_app(job_store={}, enqueue_generation=enqueue, audio_root=root, reload_audio=lambda: None,
                             password='internal-fixture-password-only', generation_enabled=True, guard_store=self.guard,
                             require_guard=True, shared_admission=admit, config_lookup=manager.lookup,
                             private_principal=principal.get, run_config_reader=lambda: None,
                             cancel_generation=lambda _: True, generation_termination_confirmed=lambda _: {},
                             cleanup_cancelled_generation=lambda *a: None)
            install_google_auth(app, client_id='fixture-client', origin=origin, owner_email='owner@gmail.com',
                                password='internal-fixture-password-only', store=Store(), clock=lambda: self.now,
                                verifier=lambda _: claims)
            with TestClient(app, base_url=origin) as client:
                self.assertEqual(client.post('/api/generate', json={'prompt': 'x'}).status_code, 401)
                challenge = client.get('/api/auth/config').json()
                claims.update(aud='fixture-client', iss='https://accounts.google.com', exp=self.now + 3600,
                              sub='new-api-subject', email='visitor@example.com', email_verified=True, nonce=challenge['nonce'])
                self.assertEqual(client.post('/api/auth/google', json={'credential': 'fixture'}, headers={'Origin': origin}).status_code, 200)
                for _ in range(3):
                    response = client.post('/api/generate', json={'prompt': 'x', 'duration': 30}, headers={'Origin': origin})
                    self.assertEqual(response.status_code, 200, response.text)
                response = client.post('/api/generate', json={'prompt': 'x', 'duration': 30, 'role': 'owner', '_owner_sub': 'owner'}, headers={'Origin': origin})
                self.assertEqual(response.status_code, 429, response.text)
                self.assertEqual(len(calls), 3)
                self.assertEqual(client.post('/api/generate', json={'prompt': 'x'}, headers={'Origin': 'https://other.example'}).status_code, 403)
