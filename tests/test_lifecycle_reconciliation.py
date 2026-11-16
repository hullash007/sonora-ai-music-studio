import copy
import itertools
import unittest
from sonora.reconciliation import reconcile_job


class LifecycleReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.admission = {'run_id': 'run', 'job_id': 'job'}
        self.claim = {**self.admission, 'claim': 'finalizing', 'claim_token': 'token'}

    def record(self, **fields):
        return {**self.admission, **fields}

    def reduce(self, receipts=None, **fields):
        return reconcile_job(admission=self.admission, receipts=receipts or {}, **fields)

    def test_all_finalization_evidence_combinations_never_publish_prematurely(self):
        for start, commit, complete, audio, available in itertools.product([False, True], repeat=5):
            receipts = {}
            if start: receipts['audio-commit-started'] = self.record(claim_token='token', operation='audio-volume-v1')
            if commit: receipts['audio-commit-completed'] = self.record(claim_token='token', operation='audio-volume-v1')
            outcome = self.record(owner_claim='finalizing', claim_token='token', result='complete') if complete else None
            result = self.reduce(receipts, terminal_claim=self.claim, terminal_result=outcome,
                                 storage_observation='own_audio_verified' if audio else 'unknown',
                                 guard_available=available)
            visible = all([start, commit, complete, audio, available])
            self.assertEqual(result['library_visibility'] == 'visible', visible)
            self.assertEqual(result['state'] == 'complete', visible)
            self.assertIn('spawn', result['forbidden_actions'])
            self.assertIn('rearm_admission', result['forbidden_actions'])

    def test_commit_started_without_completed_is_unknown(self):
        result = self.reduce({'audio-commit-started': self.record(claim_token='token', operation='audio-volume-v1')}, terminal_claim=self.claim)
        self.assertEqual(result['state'], 'audio_commit_unknown')
        self.assertEqual(result['confidence'], 'unresolved')
        self.assertEqual(result['library_visibility'], 'hidden')

    def test_cleanup_success_unknown_and_partial_failure_reconstruct_without_mutation(self):
        claim = self.record(claim='cancelled', claim_token='token')
        outcome = self.record(owner_claim='cancelled', claim_token='token', result='cancelled')
        receipts = {'function-call': self.record(call_id='call'),
                    'worker-terminal-observed': self.record(call_id='call', termination_confirmed=True, zero_activity=True, state='input_cancelled', claim_token='token'),
                    'cancel-cleanup': self.record(claim_token='token', call_id='call', operation='cancel-job-files-v1')}
        before = copy.deepcopy(receipts)
        result = self.reduce(receipts, terminal_claim=claim, terminal_result=outcome)
        self.assertEqual(result['state'], 'cleanup_outcome_unknown')
        self.assertTrue(result['worker_termination_confirmed'])
        self.assertEqual(receipts, before)
        receipts['cleanup-completed'] = self.record(claim_token='token', call_id='call', operation='cancel-job-files-v1')
        self.assertEqual(self.reduce(receipts, terminal_claim=claim, terminal_result=outcome)['state'], 'cleanup_completed')

    def test_unbound_or_conflicting_terminal_and_receipt_identity_fail_closed(self):
        variants = [self.record(owner_claim='cancelled', claim_token='token', result='complete'),
                    self.record(owner_claim='finalizing', claim_token='different', result='complete'),
                    {**self.record(owner_claim='finalizing', claim_token='token', result='complete'), 'job_id': 'other'}]
        for outcome in variants:
            result = self.reduce(terminal_claim=self.claim, terminal_result=outcome, storage_observation='own_audio_verified')
            self.assertEqual(result['state'], 'reconciliation_required')
            self.assertEqual(result['library_visibility'], 'hidden')
        result = self.reduce({'audio-commit-started': self.record(claim_token='other')}, terminal_claim=self.claim)
        self.assertEqual(result['state'], 'reconciliation_required')

    def test_cleanup_receipt_without_attempt_or_same_call_stop_is_untrusted(self):
        claim = self.record(claim='cancelled', claim_token='token')
        outcome = self.record(owner_claim='cancelled', claim_token='token', result='cancelled')
        for receipts in [{'cleanup-completed': self.record()},
                         {'cancel-cleanup': self.record(claim_token='token', call_id='call', operation='cancel-job-files-v1')},
                         {'cancel-cleanup': self.record(claim_token='token', call_id='call', operation='cancel-job-files-v1'), 'function-call': self.record(call_id='a'),
                          'worker-terminal-observed': self.record(call_id='b', termination_confirmed=True)}]:
            self.assertEqual(self.reduce(receipts, terminal_claim=claim, terminal_result=outcome)['state'], 'reconciliation_required')

    def test_submission_unknown_and_cache_independence(self):
        receipts = {'dispatch-started': self.record()}
        result = self.reduce(receipts)
        self.assertEqual(result['state'], 'submission_unknown')
        self.assertEqual(result['library_visibility'], 'hidden')
        receipts['function-call'] = self.record(call_id='real-id')
        self.assertEqual(self.reduce(receipts)['state'], 'submitted')

    def test_unknown_observations_never_grant_actions_or_completion(self):
        for observation in ['unknown', 'pending', 'returned', 'input_cancelled', 'execution_timed_out']:
            result = self.reduce({'dispatch-started': self.record()}, worker_observation=observation,
                                 storage_observation='missing')
            self.assertEqual(result['state'], 'submission_unknown')
            self.assertEqual(result['confidence'], 'unresolved')
            self.assertNotIn('delete_audio', result['allowed_actions'])
            self.assertFalse(result['worker_termination_confirmed'])

    def test_cancellation_rejection_is_not_worker_termination(self):
        claim = self.record(claim='cancelled', claim_token='token')
        outcome = self.record(owner_claim='cancelled', claim_token='token', result='cancellation_failed')
        result = self.reduce(terminal_claim=claim, terminal_result=outcome, worker_observation='pending')
        self.assertEqual(result['state'], 'cancellation_request_rejected')
        self.assertFalse(result['worker_termination_confirmed'])
        self.assertEqual(result['library_visibility'], 'hidden')
        self.assertIn('delete_audio', result['forbidden_actions'])
