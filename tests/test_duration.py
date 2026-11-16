import ast,math,tempfile,unittest,uuid
from pathlib import Path
from fastapi.testclient import TestClient
from sonora.duration import configured_durations,validate_duration,child_duration,validate_media_probe,CANDIDATE_DURATIONS
from sonora.shared import SharedAdmission,ScopedGuard,CLOSES_AT,RESERVATION,RATE
from sonora.web import create_app
from sonora.guard import read_run_config_identity
from test_shared import Store
class DurationPolicyTests(unittest.TestCase):
 def test_configuration_default_is_only_verified_length(self):
  self.assertEqual(configured_durations(),(30,))
  self.assertEqual(configured_durations('30,60,90,120'),CANDIDATE_DURATIONS)
  for invalid in ['',None,'480','60','30,30','60,30','30,121','30,true']:
   with self.subTest(invalid=invalid),self.assertRaises(ValueError):configured_durations(invalid)
 def test_candidate_boundary_and_bool_float_rejected(self):
  for x in CANDIDATE_DURATIONS:self.assertEqual(validate_duration(x,CANDIDATE_DURATIONS),x)
  for x in [True,False,10,29,31,121,480,30.0,'30',None]:
   with self.subTest(value=x),self.assertRaises(ValueError):validate_duration(x,CANDIDATE_DURATIONS)
 def test_historical_owner_child_defaults30_new_child_all_lengths(self):
  self.assertEqual(child_duration(['child','job','prompt','lyrics']),30)
  for x in CANDIDATE_DURATIONS:self.assertEqual(child_duration(['child','job','prompt','lyrics',str(x)]),x)
  for x in ['0','480','nan','30.5']:
   with self.assertRaises(ValueError):child_duration(['child','job','prompt','lyrics',x])
 def test_actual_media_duration_and_codec_not_requested_metadata_only(self):
  for d in CANDIDATE_DURATIONS:
   def probe(v,codec='mp3'):return {'streams':[{'codec_type':'audio','codec_name':codec}],'format':{'duration':str(v)}}
   self.assertAlmostEqual(validate_media_probe(probe(d+.024),d),d+.024)
   for v in [d+2.1,d-2.1,float('nan'),float('inf')]:
    with self.assertRaises(ValueError):validate_media_probe(probe(v),d)
   with self.assertRaises(ValueError):validate_media_probe(probe(d,'wav'),d)
 def test_full_execution_reserve_independent_of_track_length(self):
  self.assertLessEqual(1802*RATE,RESERVATION)
  self.assertLessEqual(math.ceil(1802*RATE*100)/100,RESERVATION)
class DurationApiTests(unittest.TestCase):
 def setup_app(self,allowed):
  s=Store();g=ScopedGuard(s);m=SharedAdmission(s,g,lambda:True,lambda _:'returned',lambda:CLOSES_AT-86400,allowed_durations=allowed);calls=[]
  def enqueue(*args):calls.append(args);return 'fc-fixture'
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  app=create_app(job_store={},enqueue_generation=enqueue,audio_root=self.tmp.name,reload_audio=lambda:None,password='fixture',generation_enabled=True,guard_store=g,run_config_reader=lambda:read_run_config_identity(g),require_guard=True,shared_admission=m.admit,config_lookup=m.lookup,private_principal=lambda:'alice',cancel_generation=lambda _:True,generation_termination_confirmed=lambda _:{},cleanup_cancelled_generation=lambda *a:None,allowed_generation_durations_seconds=allowed)
  return TestClient(app),s,m,calls
 def test_each_allowed_length_is_dispatched_exactly_without_rewriting(self):
  for d in CANDIDATE_DURATIONS:
   with self.subTest(duration=d):
    c,s,m,calls=self.setup_app(CANDIDATE_DURATIONS)
    with c:r=c.post('/api/generate',json={'prompt':'fixture','duration':d},headers={'X-Portal-Password':'fixture'})
    self.assertEqual(r.status_code,200,r.text);self.assertEqual(calls[0][3],d);self.assertEqual(len(calls),1)
 def test_unvalidated_or_invalid_length_consumes_no_slot_reservation_or_admission(self):
  for d in [60,90,120,480,True,'30',30.0,29,121]:
   with self.subTest(duration=d):
    c,s,m,calls=self.setup_app((30,))
    with c:r=c.post('/api/generate',json={'prompt':'fixture','duration':d},headers={'X-Portal-Password':'fixture'})
    self.assertEqual(r.status_code,400,r.text);self.assertEqual(s.data,{});self.assertFalse(calls)
 def test_availability_reports_deployment_lengths(self):
  c,s,m,calls=self.setup_app(CANDIDATE_DURATIONS)
  self.assertEqual(m.available()['supported_durations'],list(CANDIDATE_DURATIONS))
class DurationAdapterTests(unittest.TestCase):
 def test_child_gets_requested_duration_and_legacy_invocation_remains_accepted(self):
  root=Path(__file__).resolve().parents[1];source=(root/'infra/modal/sonora_shared.py').read_text();ast.parse(source)
  self.assertIn("lyrics,str(duration_seconds)]",source)
  self.assertIn('duration_seconds=duration_seconds',source)
  self.assertIn('validate_media_probe(probe,duration_seconds)',source)
  self.assertIn('allowed_generation_durations_seconds=available_durations',source)
  self.assertIn("'SONORA_VALIDATED_DURATIONS':os.environ.get('SONORA_VALIDATED_DURATIONS','30')",source)

class ModelParameterCompatibilityTests(unittest.TestCase):
 def test_exact_real_child_parameter_statements_without_loading_model(self):
  # Execute the actual child's parameter construction with a recorder, not model code.
  from types import SimpleNamespace
  root=Path(__file__).resolve().parents[1]
  tree=ast.parse((root/'infra/modal/owner_runtime/generation_child.py').read_text())
  assignments=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'params','generation_config'} for t in n.targets)]
  self.assertEqual(len(assignments),2)
  for d in CANDIDATE_DURATIONS:
   recorded=[]
   def record(**kw):recorded.append(kw);return SimpleNamespace(**kw)
   env={'GenerationParams':record,'GenerationConfig':record,'REQUESTED_DURATION_SECONDS':d,'prompt':'fixture music','lyrics':'[Instrumental]'}
   exec(compile(ast.Module(body=assignments,type_ignores=[]),'<actual-child-parameters>','exec'),env)
   self.assertEqual(recorded[0]['duration'],float(d));self.assertEqual(recorded[1]['batch_size'],1)
   self.assertFalse(recorded[1]['allow_lm_batch']);self.assertEqual(recorded[1]['audio_format'],'mp3')

class OwnerDurationVisibilityTests(unittest.TestCase):
 def test_owner_validation_does_not_enable_other_users_or_anonymous(self):
  from sonora.duration import visible_durations
  for identity in [None,{}, {'role':'visitor'}, {'role':'user'}]:
   self.assertEqual(visible_durations((30,),identity,True),(30,))
  self.assertEqual(visible_durations((30,),{'role':'owner'},False),(30,))
  self.assertEqual(visible_durations((30,),{'role':'owner'},True),(30,120))
 def test_dynamic_durations_checked_before_admission(self):
  fixture=DurationApiTests();fixture.setUp();c,s,m,calls=fixture.setup_app(lambda:(30,))
  try:
   with c:r=c.post('/api/generate',json={'prompt':'fixture','duration':120},headers={'X-Portal-Password':'fixture'})
   self.assertEqual(r.status_code,400);self.assertFalse(s.data);self.assertFalse(calls)
  finally:fixture.doCleanups()
