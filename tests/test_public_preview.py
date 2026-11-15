"""Public staging is a read-only surface, never an unlocked worker."""
import tempfile
import unittest
from fastapi.testclient import TestClient
from sonora.web import create_app


class PublicPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.kwargs = dict(
            job_store={}, enqueue_generation=self.forbidden,
            audio_root=self.tmp.name, reload_audio=lambda: None,
            password='server-secret', public_preview=True,
        )
        self.client = TestClient(create_app(**self.kwargs))

    def forbidden(self, *args):
        self.calls.append(args)
        raise AssertionError('Preview attempted generation')

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_open_page_health_and_empty_library(self):
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('type="password"', page.text)
        self.assertNotIn('server-secret', page.text)
        self.assertNotIn('password()', page.text)
        self.assertNotIn('X-Portal-Password', page.text)
        self.assertIn('Generation unavailable in preview', page.text)
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        library = self.client.get('/api/library')
        self.assertEqual(library.status_code, 200)
        self.assertEqual(library.json()['tracks'], [])

    def test_all_mutations_denied_before_callbacks(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            for path in ('/api/generate', '/api/cancel/example', '/api/library', '/'):
                self.assertEqual(self.client.request(method, path, json={'prompt':'test'}).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_admin_docs_and_status_not_public(self):
        for path in ('/docs', '/redoc', '/openapi.json', '/api/status/example', '/admin', '/api/audio/example'):
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_head_and_synthetic_audio(self):
        import uuid
        from pathlib import Path
        from sonora.library import write_track_record
        job_id = str(uuid.uuid4())
        audio = Path(self.tmp.name) / 'jobs' / job_id / 'tone.wav'
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b'RIFF-synthetic-fixture')
        write_track_record(self.tmp.name, dict(job_id=job_id, status='complete',
            file=f'jobs/{job_id}/tone.wav', filename='tone.wav', title='Synthetic test',
            created_at=1800000000.0, completed_at=1800000001.0,
            duration_seconds=2, prompt='Test', lyrics='', engine='Fixture', model_revision='none'))
        for path in ('/', '/api/health', '/api/library', f'/api/library/{job_id}/audio'):
            self.assertEqual(self.client.get(path).status_code, 200)
            head = self.client.head(path)
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.content, b'')

    def test_preview_cannot_enable_generation(self):
        with self.assertRaisesRegex(ValueError, 'cannot enable generation'):
            create_app(**self.kwargs, generation_enabled=True)


if __name__ == '__main__':
    unittest.main()
