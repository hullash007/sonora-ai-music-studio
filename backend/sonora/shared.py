"""Credit-bounded shared admission. Every job has a new immutable single-run guard.

No user quotas. Serialized admission and an atomic durable busy slot prevent a
second in-flight allocation. Reservations fail closed on missing settlement.
"""
from contextvars import ContextVar
import math,time,uuid
from sonora.guard import RUN_CONFIG_KEY,RUN_CONFIG_KIND,read_run_config_identity,claim_one_attempt

# Explicitly expires before the verified November 2026 credit cycle rolls over.
CLOSES_AT=1796063400.0  # 2026-11-30 18:30 UTC (Dec 1 00:00 IST), minus safety below
BUDGET=26.13  # $28 total-cycle ceiling less verified $1.87 pre-existing usage
RESERVATION=0.95
RATE=0.000306+8*0.0000131+32*0.00000222

class ScopedGuard:
 def __init__(self,store):
  self.store=store;self.selected=ContextVar('run-'+str(id(self)),default=None)
 def select(self,run):
  self.selected.set(run)
 def _key(self,key):
  if key!=RUN_CONFIG_KEY:return key
  run=self.selected.get()
  if not run:raise RuntimeError('No job guard selected')
  return 'shared-config:'+run
 def get(self,key,default=None):return self.store.get(self._key(key),default)
 def put(self,key,value,**kwargs):return self.store.put(self._key(key),value,**kwargs)

class SharedAdmission:
 def __init__(self,store,guard,stats,call_state,clock=time.time):
  self.store=store;self.guard=guard;self.stats=stats;self.call_state=call_state;self.clock=clock
 def lookup(self,run):
  self.guard.select(run);return read_run_config_identity(self.guard)
 def spent(self):
  total=0.0
  for key in self.store.keys():
   if not key.startswith('credit-reservation:'):continue
   value=self.store.get(key)
   amount=float(value['charged_bound'])
   if not math.isfinite(amount) or not 0<amount<=RESERVATION:raise RuntimeError('Credit ledger needs review')
   total+=amount
  return total
 def reconcile(self):
  busy=self.store.get('shared-busy')
  if not busy:return
  result=self.store.get('worker-finished:'+busy['job_id'])
  call=self.store.get('shared-call:'+busy['job_id'])
  # Never unlock on timeout alone, an enqueue exception, or merely a cancel ACK.
  if result and call and self.call_state(call) in {'returned','execution_timed_out'} and self.stats():
   self.store.pop('shared-busy')
 def available(self):
  self.reconcile()
  return {'available':self.clock()<CLOSES_AT-3600 and self.spent()+RESERVATION<=BUDGET and not self.store.get('shared-busy'),'busy':bool(self.store.get('shared-busy')),'credit_limited':self.spent()+RESERVATION>BUDGET,'expired':self.clock()>=CLOSES_AT-3600,'duration_seconds':30}
 def admit(self,job,fingerprint):
  from fastapi import HTTPException
  if self.clock()>=CLOSES_AT-3600:raise HTTPException(503,'Music creation is temporarily unavailable. You can still listen to your saved tracks.')
  self.reconcile()
  if not self.store.put('shared-busy',{'job_id':job,'at':self.clock()},skip_if_exists=True):raise HTTPException(409,'The studio is busy creating music. Please try again in a few minutes.')
  # Fail closed: an unexpected storage error after acquiring busy needs review.
  if self.spent()+RESERVATION>BUDGET:
   self.store.pop('shared-busy');raise HTTPException(503,'Music creation is temporarily unavailable. You can still listen to your saved tracks.')
  self.store.put('credit-reservation:'+job,{'charged_bound':RESERVATION},skip_if_exists=True)
  now=self.clock();run=str(uuid.uuid4());self.guard.select(run)
  if not self.guard.put(RUN_CONFIG_KEY,{'kind':RUN_CONFIG_KIND,'schema_version':1,'run_id':run,'issued_at':now,'expires_at':now+3600,'max_attempts':1},skip_if_exists=True):raise RuntimeError('Duplicate guard')
  return claim_one_attempt(self.guard,job,request_sha256=fingerprint,clock=self.clock)
 def finish(self,job,elapsed):
  # Include full 600-second startup allocation, configured runtime resources,
  # and container scale-down. Round upwards. Failure retains full reservation.
  bound=min(RESERVATION,math.ceil((max(0,elapsed)+602)*RATE*100)/100)
  self.store.put('credit-reservation:'+job,{'charged_bound':bound})
  self.store.put('worker-finished:'+job,{'at':self.clock()})
