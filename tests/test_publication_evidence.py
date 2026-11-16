import unittest
import test_api as fixtures
from sonora.guard import terminal_result_key, terminal_claim_key
from sonora.worker import run_guarded_generation


class PublicationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.c = fixtures.GuardedSonoraApiTests(); self.c.setUp()
        self.job = self.c.submit().json()['job_id']
        audio = self.c.audio_root / 'jobs' / self.job / 'fixture.mp3'
        audio.parent.mkdir(parents=True); audio.write_bytes(b'fake')
        result = run_guarded_generation(guard_store=self.c.guard_store,
                    run_config=self.c.run_config, job_id=self.job,
                    audio_root=self.c.audio_root, prompt='Test', lyrics='',
                    duration_seconds=30, generate_audio=lambda:audio,
                    commit_audio=lambda:None, engine='fake', model_revision='fake',
                    created_at=self.c.now)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.library().json()['count'], 1)

    def tearDown(self):
        self.c.tearDown()

    def library(self):
        return self.c.client.get('/api/library', headers=fixtures.AUTH_HEADERS)

    def test_terminal_outcome_wrong_token_rejected_before_publication(self):
        key = terminal_result_key(self.c.run_config.run_id, self.job)
        self.c.guard_store.data[key] = {**self.c.guard_store.data[key], 'claim_token':'0'*32}
        self.assertEqual(self.library().status_code, 503)
        self.assertEqual(self.c.client.get('/api/library/'+self.job+'/audio', headers=fixtures.AUTH_HEADERS).status_code, 503)

    def test_other_claim_commit_receipt_is_hidden(self):
        key = next(k for k in self.c.guard_store.data if k.endswith(':audio-commit-completed'))
        self.c.guard_store.data[key] = {**self.c.guard_store.data[key], 'claim_token':'0'*32}
        self.assertEqual(self.library().json()['count'], 0)

    def test_other_job_commit_receipt_is_rejected(self):
        key = next(k for k in self.c.guard_store.data if k.endswith(':audio-commit-completed'))
        self.c.guard_store.data[key] = {**self.c.guard_store.data[key], 'job_id':'other'}
        self.assertEqual(self.library().status_code, 503)

    def test_canonical_terminal_claim_is_produced_by_guard_and_required(self):
        key = terminal_claim_key(self.c.run_config.run_id, self.job)
        self.assertEqual(self.c.guard_store.data[key]['claim'], 'finalizing')
        # Malformed fixture only, not permission to remove real history.
        self.c.guard_store.data.pop(key)
        self.assertEqual(self.library().json()['count'], 0)

    def test_matching_token_wrong_operation_never_publishes(self):
        key = next(k for k in self.c.guard_store.data if k.endswith(':audio-commit-completed'))
        self.c.guard_store.data[key] = {**self.c.guard_store.data[key], 'operation':'different-volume-operation'}
        self.assertEqual(self.library().json()['count'], 0)

    def test_registered_archive_retains_original_publication_proof(self):
        from sonora.web import create_app
        from fastapi.testclient import TestClient
        from sonora.guard import read_run_config_identity
        owner = fixtures.FakeAtomicGuardStore()
        app = create_app(job_store={}, enqueue_generation=lambda *a:None,
            audio_root=self.c.audio_root,reload_audio=lambda:None,password=fixtures.TEST_PASSWORD,
            cancel_generation=lambda *a:False,guard_store=owner,run_config_reader=lambda:read_run_config_identity(owner),
            archived_run_guards={self.c.run_config.run_id:(self.c.guard_store,lambda:self.c.run_config)})
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/library',headers=fixtures.AUTH_HEADERS).json()['count'],1)
            self.assertEqual(client.get('/api/library/'+self.job+'/audio',headers=fixtures.AUTH_HEADERS).status_code,200)
            self.assertEqual(owner.data,{})
            key=terminal_claim_key(self.c.run_config.run_id,self.job)
            self.c.guard_store.data.pop(key)
            self.assertEqual(client.get('/api/library',headers=fixtures.AUTH_HEADERS).json()['count'],0)
            self.assertEqual(client.get('/api/library/'+self.job+'/audio',headers=fixtures.AUTH_HEADERS).status_code,404)
