import sys,importlib.util,os,tempfile,time,uuid,unittest
from pathlib import Path
from test_worker import FakeGuardDict
from sonora.guard import RUN_CONFIG_KEY,RUN_CONFIG_KIND
from fastapi.testclient import TestClient
class OwnerTests(unittest.TestCase):
 def test_owner_auth_and_limits(self):
  p=Path(__file__).resolve().parents[1]/'infra/modal/sonora_owner.py';s=importlib.util.spec_from_file_location('owner_fixture',p);m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
  g=FakeGuardDict();m.guard=g;m.jobs={}
  with tempfile.TemporaryDirectory() as tmp:
   m.AUDIO=tmp
   class Audio:
    def reload(self):pass
   m.audio=Audio();os.environ['PORTAL_PASSWORD']='fixture-test-only'
   c=TestClient(m.music_portal.local());h={'X-Portal-Password':'fixture-test-only'}
   self.assertEqual(c.get('/api/library').status_code,401);self.assertEqual(c.get('/api/owner-session').status_code,401);self.assertEqual(c.get('/docs').status_code,404)
   self.assertFalse(c.get('/api/owner-session',headers=h).json()['started'])
   self.assertEqual(c.post('/api/owner-session/start').status_code,401)
   first=c.post('/api/owner-session/start',headers=h).json();self.assertTrue(first['started'])
   again=c.post('/api/owner-session/start',headers=h).json();self.assertEqual(first['admission_closes_at'],again['admission_closes_at'])
   self.assertEqual(g.get(RUN_CONFIG_KEY)['max_attempts'],1)
   self.assertEqual(c.post('/api/generate',json={'prompt':'test','duration':31},headers=h).status_code,400)
   self.assertEqual(c.get('/').status_code,200)
   # A failed enqueue still consumes the sole admission; start cannot reset it.
   class Worker:
    def spawn(self,*args):raise RuntimeError('synthetic queue failure, no compute')
   m.generate_one=Worker()
   response=c.post('/api/generate',json={'prompt':'synthetic test','duration':30},headers=h)
   self.assertIn(response.status_code,[200,503])
   after=c.post('/api/owner-session/start',headers=h).json()
   self.assertTrue(after['consumed']);self.assertEqual(first['admission_closes_at'],after['admission_closes_at'])
   self.assertEqual(c.post('/api/generate',json={'prompt':'second','duration':30},headers=h).status_code,409)

