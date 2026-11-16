import tempfile,unittest,uuid,time
from pathlib import Path
from fastapi.testclient import TestClient
from sonora.web import create_app
from sonora.google_auth import install_google_auth,principal
from sonora.library import write_track_record
from test_api import FakeAtomicGuardStore
class PrivateUserTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.jobs={};self.store=FakeAtomicGuardStore();self.claims={}
  app=create_app(job_store=self.jobs,enqueue_generation=lambda *a:None,audio_root=self.root,reload_audio=lambda:None,password='internal',generation_enabled=True,google_auth_enabled=True,private_principal=principal.get)
  install_google_auth(app,client_id='client',origin='https://studio.test',owner_email='owner@gmail.com',password='internal',store=self.store,verifier=lambda _:self.claims)
  self.a=TestClient(app,base_url='https://studio.test');self.b=TestClient(app,base_url='https://studio.test')
  self.login(self.a,'alice');self.login(self.b,'bob')
 def login(self,c,sub):
  cfg=c.get('/api/auth/config').json();self.claims={'aud':'client','iss':'https://accounts.google.com','exp':time.time()+3600,'email_verified':True,'sub':sub,'email':sub+'@gmail.com','nonce':cfg['nonce']}
  self.assertEqual(c.post('/api/auth/google',json={'credential':'fixture'},headers={'Origin':'https://studio.test'}).status_code,200)
 def tearDown(self):self.a.close();self.b.close();self.tmp.cleanup()
 def test_private_jobs_library_and_audio_all_aliases(self):
  r=self.a.post('/api/generate',json={'prompt':'Alice private prompt','duration':30},headers={'Origin':'https://studio.test'});self.assertEqual(r.status_code,200);job=r.json()['job_id']
  self.assertEqual(self.jobs[job]['_owner_sub'],'alice')
  file=self.root/'jobs'/job/'test.mp3';file.parent.mkdir(parents=True);file.write_bytes(b'ID3-fixture')
  write_track_record(self.root,{'job_id':job,'status':'complete','prompt':'Alice private prompt','file':'jobs/'+job+'/test.mp3','duration_seconds':30})
  self.assertEqual(self.a.get('/api/library').json()['count'],1)
  self.assertEqual(self.b.get('/api/library').json()['count'],0)
  for path in ['/api/status/','/api/audio/','/api/download/']:
   method=self.b.post if path=='/api/cancel/' else self.b.get
   kwargs={'headers':{'Origin':'https://studio.test'}} if path=='/api/cancel/' else {}
   self.assertEqual(method(path+job,**kwargs).status_code,404,path)
  self.assertEqual(self.b.get('/api/library/'+job+'/audio').status_code,404)
  self.assertEqual(self.a.get('/api/library/'+job+'/audio').status_code,200)
 def test_unknown_legacy_owner_track_hidden(self):
  job=str(uuid.uuid4());self.jobs[job]={'status':'complete','prompt':'legacy private'}
  self.assertEqual(self.a.get('/api/status/'+job).status_code,404)
