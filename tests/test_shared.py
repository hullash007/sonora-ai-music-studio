import unittest,uuid
from fastapi import HTTPException
from sonora.shared import SharedAdmission,ScopedGuard,BUDGET,RESERVATION,CLOSES_AT
from sonora.guard import read_run_config_identity
from test_api import FakeAtomicGuardStore
class Store(FakeAtomicGuardStore):
 def keys(self):return list(self.data)
 def pop(self,k):return self.data.pop(k)
class SharedTests(unittest.TestCase):
 def setUp(self):
  self.s=Store();self.g=ScopedGuard(self.s);self.now=CLOSES_AT-86400
  self.m=SharedAdmission(self.s,self.g,lambda:True,lambda c:'returned',lambda:self.now)
 def job(self):return str(uuid.uuid4())
 def test_second_job_busy_and_unproven_expiry_never_unlocks(self):
  self.m.admit(self.job(),'a'*64);self.now+=4000
  with self.assertRaises(HTTPException):self.m.admit(self.job(),'b'*64)
 def test_each_job_immutable_guard_and_settlement(self):
  j=self.job();first=self.m.admit(j,'a'*64);self.s.put('shared-call:'+j,'call');self.m.finish(j,30)
  second=self.m.admit(self.job(),'b'*64);self.assertNotEqual(first.run_id,second.run_id)
  self.assertEqual(first,self.m.lookup(first.run_id));self.assertEqual(first,read_run_config_identity(self.g))
 def test_exhaustion_blocks_and_does_not_rearm(self):
  
  for i in range(28):self.s.put('credit-reservation:previous'+str(i),{'charged_bound':RESERVATION})
  with self.assertRaises(HTTPException):self.m.admit(self.job(),'a'*64)
  self.assertFalse(self.s.get('shared-busy'))
 def test_cycle_cutoff(self):
  self.now=CLOSES_AT
  with self.assertRaises(HTTPException):self.m.admit(self.job(),'a'*64)
  self.assertEqual(self.s.data,{})
 def test_failclosed_full_reservation_without_worker_finish(self):
  self.m.admit(self.job(),'a'*64);self.assertEqual(self.m.spent(),RESERVATION)

class SharedWebFlowTests(unittest.TestCase):
 def test_repeat_generation_uses_separate_configs_and_retains_previous_status(self):
  import tempfile
  from fastapi.testclient import TestClient
  from sonora.web import create_app
  from sonora.recovery import read_receipt
  s=Store();g=ScopedGuard(s);m=SharedAdmission(s,g,lambda:True,lambda c:'returned')
  jobs={};calls=[]
  def enqueue(job,*_):
   cfg=read_run_config_identity(g);calls.append((job,cfg));s.put('shared-call:'+job,'fc-fixture-'+str(len(calls)));return 'fc-fixture-'+str(len(calls))
  with tempfile.TemporaryDirectory() as root:
   app=create_app(job_store=jobs,enqueue_generation=enqueue,audio_root=root,reload_audio=lambda:None,password='fixture',generation_enabled=True,guard_store=g,run_config_reader=lambda:read_run_config_identity(g),cancel_generation=lambda _:True,require_guard=True,generation_termination_confirmed=lambda _:{},cleanup_cancelled_generation=lambda *a:None,shared_admission=m.admit,config_lookup=m.lookup,private_principal=lambda:'alice')
   with TestClient(app) as c:
    headers={'X-Portal-Password':'fixture'}
    first=c.post('/api/generate',json={'prompt':'fixture','duration':30},headers=headers);self.assertEqual(first.status_code,200,first.text);j=first.json()['job_id']
    self.assertEqual(c.post('/api/generate',json={'prompt':'second','duration':30},headers=headers).status_code,409)
    m.finish(j,30)
    second=c.post('/api/generate',json={'prompt':'second','duration':30},headers=headers);self.assertEqual(second.status_code,200,second.text)
    self.assertNotEqual(calls[0][1].run_id,calls[1][1].run_id)
    # Old jobs still validate against their own sentinel after newer admission.
    self.assertEqual(c.get('/api/status/'+j,headers=headers).status_code,200)
    self.assertEqual(jobs[j]['_owner_sub'],'alice')
    self.assertIsNotNone(read_receipt(g,m.lookup(calls[0][1].run_id),j,'function-call'))

class ConcurrentAdmissionTests(unittest.TestCase):
 def test_two_users_race_exactly_one_reserves_and_admits(self):
  from concurrent.futures import ThreadPoolExecutor
  from threading import Barrier
  from sonora.google_auth import principal
  s=Store();g=ScopedGuard(s);m=SharedAdmission(s,g,lambda:True,lambda c:'returned');barrier=Barrier(2)
  def user(sub):
   principal.set(sub);barrier.wait()
   try:return m.admit(str(uuid.uuid4()),'a'*64)
   except HTTPException as e:return e.status_code
  with ThreadPoolExecutor(2) as pool:results=list(pool.map(user,['alice','bob']))
  self.assertEqual(sum(x==409 for x in results),1)
  self.assertEqual(len([k for k in s.keys() if k.startswith('credit-reservation:')]),1)
  self.assertEqual(len([k for k in s.keys() if k.startswith('shared-config:')]),1)
  self.assertEqual(len([k for k in s.keys() if k.endswith(':admission')]),1)
 def test_failure_after_busy_acquisition_cannot_admit_second_worker(self):
  s=Store();g=ScopedGuard(s);m=SharedAdmission(s,g,lambda:True,lambda c:'returned')
  original=s.put
  def fail(key,*args,**kwargs):
   if key.startswith('credit-reservation:'):raise RuntimeError('storage fault fixture')
   return original(key,*args,**kwargs)
  s.put=fail
  with self.assertRaises(RuntimeError):m.admit(str(uuid.uuid4()),'a'*64)
  s.put=original
  with self.assertRaises(HTTPException):m.admit(str(uuid.uuid4()),'b'*64)
  self.assertIsNotNone(s.get('shared-busy'))

class WorkerDependencyTests(unittest.TestCase):
 def test_shared_worker_import_does_not_need_fastapi(self):
  import subprocess,sys,os
  code='''import builtins
original=builtins.__import__
def guarded(name,*a,**k):
 if name=='fastapi' or name.startswith('fastapi.'):raise ImportError('HTTP dependency forbidden on GPU worker')
 return original(name,*a,**k)
builtins.__import__=guarded
from sonora.shared import ScopedGuard,SharedAdmission
'''
  r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
  self.assertEqual(r.returncode,0,r.stderr)
