import tempfile
import unittest
from fastapi.testclient import TestClient
from sonora.web import create_app

class FrontendDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.client=TestClient(create_app(job_store={},enqueue_generation=lambda *a:None,audio_root=self.tmp.name,reload_audio=lambda:None,password='secret',public_preview=True))
    def tearDown(self):
        self.client.close();self.tmp.cleanup()
    def test_exact_built_assets_and_headers(self):
        for path,media in [('/assets/app.js','javascript'),('/assets/app.css','text/css')]:
            r=self.client.get(path)
            self.assertEqual(r.status_code,200)
            self.assertIn(media,r.headers['content-type'])
            self.assertEqual(r.headers['x-robots-tag'],'noindex, nofollow')
            self.assertEqual(self.client.head(path).content,b'')
        for path in ['/assets/package.json','/assets/../web.py','/assets/main.jsx','/static/index.html']:
            self.assertEqual(self.client.get(path).status_code,404)
    def test_assets_cannot_be_mutated(self):
        for method in ['POST','PUT','PATCH','DELETE','OPTIONS']:
            self.assertEqual(self.client.request(method,'/assets/app.js').status_code,403)
