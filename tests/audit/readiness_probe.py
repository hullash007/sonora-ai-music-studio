"""Local-only characterization of readiness gaps and corrections, not enablement.
No Modal import, GPU, model or service calls. All generation/audio are local fakes.
Run with the repository's API-test dependencies. Remove/replace expected findings
when the reviewed fixes land; never treat this output as generation readiness.
"""
import sys,tempfile,pathlib,json,time,uuid,threading,concurrent.futures
root=pathlib.Path(__file__).resolve().parents[2];sys.path[:0]=[str(root/'backend'),str(root/'tests')]
from test_worker import FakeGuardDict
from sonora.guard import RUN_CONFIG_KEY,RUN_CONFIG_KIND,claim_one_attempt,read_terminal_state,terminal_result_key
from sonora.worker import run_guarded_generation
from sonora.web import create_app
from fastapi.testclient import TestClient

def fixture():
 g=FakeGuardDict();now=time.time();g.put(RUN_CONFIG_KEY,{'kind':RUN_CONFIG_KIND,'schema_version':1,'run_id':str(uuid.uuid4()),'issued_at':now,'expires_at':now+3600,'max_attempts':1});return g
findings={}
with tempfile.TemporaryDirectory() as d:
 g=fixture();jobs={};spawned=[]
 def enqueue(*args):spawned.append(args);raise TimeoutError('Simulated lost RPC response after remote acceptance')
 app=create_app(job_store=jobs,enqueue_generation=enqueue,audio_root=d,reload_audio=lambda:None,password='qa',guard_store=g,run_config_reader=lambda:None,cancel_generation=lambda call:True,generation_enabled=True)
 with TestClient(app) as c:response=c.post('/api/generate',json={'prompt':'Audit','duration':30},headers={'X-Portal-Password':'qa'})
 job=next(iter(jobs.values()));from sonora.guard import read_run_config_identity
 state=read_terminal_state(g,read_run_config_identity(g),job['job_id']);findings['lost_spawn_response']={'response':response.status_code,'spawn_called':len(spawned),'guard':state['state'] if state else None,'submission_status':response.json().get('status'),'call_id_persisted':any(k.endswith(':function-call') for k in g.data),'actual_remote_termination_known':False}
with tempfile.TemporaryDirectory() as d:
 g=fixture();job=str(uuid.uuid4());config=claim_one_attempt(g,job);audio=pathlib.Path(d)/'jobs'/job/'test.mp3';audio.parent.mkdir(parents=True);entered=threading.Event();release=threading.Event();calls=[]
 def generate():calls.append(1);entered.set();release.wait(timeout=5);audio.write_bytes(b'fake-audio');return audio
 def run():return run_guarded_generation(guard_store=g,run_config=config,job_id=job,audio_root=d,prompt='Audit',lyrics='',duration_seconds=30,generate_audio=generate,commit_audio=lambda:None,engine='fake',model_revision='fake',created_at=time.time())
 with concurrent.futures.ThreadPoolExecutor(2) as pool:
  first=pool.submit(run);assert entered.wait(timeout=5)
  second=pool.submit(run).result(timeout=5);release.set();results=[first.result(timeout=5),second]
 findings['duplicate_worker_entry']={'generation_callbacks':len(calls),'results':[r['status'] for r in results]}
with tempfile.TemporaryDirectory() as d:
 g=fixture();job=str(uuid.uuid4());config=claim_one_attempt(g,job);audio=pathlib.Path(d)/'jobs'/job/'test.mp3';audio.parent.mkdir(parents=True);audio.write_bytes(b'fake-audio');during=[]
 app=create_app(job_store={},enqueue_generation=lambda *a:None,audio_root=d,reload_audio=lambda:None,password='qa',public_preview=True,guard_store=g,run_config_reader=lambda:config,cancel_generation=lambda _:True)
 def commit():
  with TestClient(app) as c:during.append(c.get('/api/library').json()['count'])
 run_guarded_generation(guard_store=g,run_config=config,job_id=job,audio_root=d,prompt='Audit',lyrics='',duration_seconds=30,generate_audio=lambda:audio,commit_audio=commit,engine='fake',model_revision='fake',created_at=time.time())
 findings['preterminal_library_visibility']={'library_count_inside_commit_before_outcome':during,'final_guard':read_terminal_state(g,config,job)['state']}
print(json.dumps(findings,indent=2))

# Cancellation boundary evidence is characterization, never a desired contract.
from test_api import GuardedSonoraApiTests, AUTH_HEADERS
for suffix, label in [(':cancel-dispatch', 'crash_before_cancel_call'),
                      (':cancel-dispatch-result', 'lost_cancel_acceptance_receipt')]:
 case=GuardedSonoraApiTests();case.setUp();case.termination_confirmed=False
 try:
  job=case.submit().json()['job_id'];original=case.guard_store.put
  def interrupted_put(key,value,*,skip_if_exists=False):
   if key.endswith(suffix):
    if suffix==':cancel-dispatch':
     original(key,value,skip_if_exists=skip_if_exists)
    raise TimeoutError('Simulated portal interruption at cancellation boundary')
   return original(key,value,skip_if_exists=skip_if_exists)
  case.guard_store.put=interrupted_put
  response=case.client.post('/api/cancel/'+job,headers=AUTH_HEADERS)
  case.guard_store.put=original
  status=case.client.get('/api/status/'+job,headers=AUTH_HEADERS)
  print(json.dumps({label:{'cancel_http':response.status_code,'remote_cancel_calls':len(case.cancel_calls),
                          'status_after_restart':status.json()['status'],
                          'cleanup_calls':len(case.cleanup_calls)}}))
 finally:
  case.tearDown()
