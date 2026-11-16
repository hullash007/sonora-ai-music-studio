"""Google-authenticated, private multi-user generation. Preserves read-only preview."""
import os,sys,json,time
from pathlib import Path
import modal
HERE=Path(__file__).resolve().parent
BASE=HERE.parent.parent
SOURCE='/opt/ACE-Step-1.5'
PACK='/opt/sonora-validation-prep'
BACKEND='/opt/sonora/backend'
CHECKPOINTS=SOURCE+'/checkpoints'
AUDIO='/mnt/sonora-private-audio'
NAME='sonora-owner-session-20261116'
app=modal.App(NAME)
checkpoints=modal.Volume.from_name(os.environ.get('SONORA_CHECKPOINT_VOLUME','sonora-checkpoints'),create_if_missing=False)
audio=modal.Volume.from_name(NAME+'-audio',create_if_missing=True,version=2)
shared_store=modal.Dict.from_name(NAME+'-shared-credit-20261116',create_if_missing=True)
archive_guard=modal.Dict.from_name(os.environ.get('SONORA_ARCHIVE_GUARD','sonora-configure-archive-guard'),create_if_missing=False)
jobs=modal.Dict.from_name(NAME+'-jobs',create_if_missing=True)
secret=modal.Secret.from_name('sonora-owner-session-portal')
image=modal.Image.from_id(os.environ.get('SONORA_IMAGE_ID','im-configure-pinned-image'))
# Identity dependencies are isolated to HTTP image; GPU uses the tested image unchanged.
web_image=None
if modal.is_local():
 image=image.add_local_dir(str(BASE/'backend'),remote_path=BACKEND,copy=True,ignore=['__pycache__']).add_local_dir(str(HERE/'owner_runtime'),remote_path='/opt/sonora-private',copy=True,ignore=['__pycache__'])
@app.function(image=image,block_network=True,gpu=['A10','L4'],cpu=(8,8),memory=(32768,32768),timeout=1200,startup_timeout=600,retries=0,min_containers=0,max_containers=1,scaledown_window=2,single_use_containers=True,volumes={CHECKPOINTS:checkpoints,AUDIO:audio},env={'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
def generate_one(job_id,prompt,lyrics,run_id):
    import sys,subprocess,os,time,signal
    sys.path.insert(0,BACKEND)
    from sonora.guard import read_run_config_identity,read_terminal_state
    from sonora.worker import run_guarded_generation,assert_job_active,GenerationStopped
    from sonora.shared import ScopedGuard,SharedAdmission
    guard=ScopedGuard(shared_store);guard.select(run_id)
    config=read_run_config_identity(guard)
    worker_started=time.time()
    Path(AUDIO,'receipts').mkdir(parents=True,exist_ok=True)
    def generate():
        assert_job_active(guard,config,job_id)
        env=os.environ.copy();env['PYTHONPATH']=PACK+':/opt/sonora-private'
        logpath=Path(AUDIO,'receipts/'+job_id+'-generation-child.log')
        with logpath.open('x') as log:
            p=subprocess.Popen([SOURCE+'/.venv/bin/python','/opt/sonora-private/generation_child.py',job_id,prompt,lyrics],cwd=SOURCE,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            started=time.monotonic()
            try:
                while p.poll() is None:
                    assert_job_active(guard,config,job_id)
                    if time.monotonic()-started>1100:raise TimeoutError('Private generation child deadline')
                    time.sleep(1)
            except BaseException:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.wait();raise
            if p.returncode:raise RuntimeError('Model child failed, exit '+str(p.returncode)+'; preserved stage log')
        text=logpath.read_text()
        outputs=[s.removeprefix('OUTPUT_PATH ') for s in text.splitlines() if s.startswith('OUTPUT_PATH ')]
        if len(outputs)!=1:raise RuntimeError('Expected exactly one private model output')
        file=Path(outputs[0])
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(file)],text=True,timeout=20))
        tracks=[s for s in probe['streams'] if s.get('codec_type')=='audio']
        duration=float(probe['format']['duration'])
        if len(tracks)!=1 or tracks[0].get('codec_name')!='mp3' or not 29<=duration<=32:
            raise RuntimeError('Generated media codec/duration gate failed')
        subprocess.run(['ffmpeg','-v','error','-i',str(file),'-f','null','-'],check=True,timeout=30)
        import shutil,hashlib
        evidence=Path(AUDIO,'retained-media');evidence.mkdir(exist_ok=True)
        retained=evidence/file.name;shutil.copyfile(file,retained)
        Path(AUDIO,'receipts/'+job_id+'-retained-media.json').write_text(json.dumps({'file':str(retained.relative_to(Path(AUDIO))),'sha256':hashlib.sha256(retained.read_bytes()).hexdigest(),'bytes':retained.stat().st_size},indent=2))
        audio.commit()
        Path(AUDIO,'receipts/'+job_id+'-media-probe.json').write_text(json.dumps(probe,indent=2))
        sys.path.insert(0,'/opt/sonora-private')
        from output_adapter import relative_job_output
        return relative_job_output(AUDIO,job_id,file)
    result=run_guarded_generation(guard_store=guard,run_config=config,job_id=job_id,audio_root=AUDIO,prompt=prompt,lyrics=lyrics,duration_seconds=30,generate_audio=generate,commit_audio=audio.commit,engine='ACE-Step 1.5 - shared studio',model_revision='19671f406d603126926c1b7e2adc169acbcade22',created_at=time.time())
    # Failure logs live outside per-job cleanup, so retain them even after a model failure.
    audio.commit()
    SharedAdmission(shared_store,guard,lambda:False,lambda _:None).finish(job_id,time.time()-worker_started)
    return result


web_image=image.pip_install('google-auth==2.62.2','requests==2.34.2')
@app.function(image=web_image,cpu=(0.25,0.25),memory=(512,512),timeout=30,startup_timeout=120,min_containers=0,max_containers=1,scaledown_window=10,volumes={AUDIO:audio},secrets=[secret])
@modal.concurrent(max_inputs=1)
@modal.asgi_app()
def music_portal():
 sys.path[:0]=[BACKEND,SOURCE+'/.venv/lib/python3.12/site-packages']
 from sonora.web import create_app
 from sonora.guard import read_run_config_identity
 from sonora.shared import ScopedGuard,SharedAdmission
 from sonora.google_auth import principal
 guard=ScopedGuard(shared_store)
 def enqueue(job,prompt,lyrics,duration):
  if duration!=30:raise ValueError('Only30-second owner session supported')
  cfg=read_run_config_identity(guard)
  handle=generate_one.spawn(job,prompt,lyrics,cfg.run_id)
  shared_store.put('shared-call:'+job,handle.object_id)
  return handle
 def cancel(call):
  modal.FunctionCall.from_id(call).cancel(terminate_containers=True);return True
 def call_state(call):
  try:modal.FunctionCall.from_id(call).get(timeout=0);return 'returned'
  except modal.exception.FunctionTimeoutError:return 'execution_timed_out'
  except TimeoutError:return 'pending'
  except BaseException:return 'unknown'
 def termination(call):
  state=call_state(call);zero=False
  if state in {'returned','execution_timed_out'}:
   st=generate_one.get_current_stats();zero=st.backlog==st.num_running_inputs==st.num_total_runners==0
  return {'call_id':call,'state':state,'zero_activity':zero,'termination_confirmed':zero}
 def no_cancellation_cleanup(*args):raise RuntimeError('Cancellation reason is unverified; output retained')
 manager=SharedAdmission(shared_store,guard,lambda:(lambda st:st.backlog==st.num_running_inputs==st.num_total_runners==0)(generate_one.get_current_stats()),call_state)
 google_client_id=os.environ['SONORA_GOOGLE_CLIENT_ID']
 api=create_app(shared_admission=manager.admit,config_lookup=manager.lookup,private_principal=principal.get,google_auth_enabled=bool(google_client_id),job_store=jobs,enqueue_generation=enqueue,audio_root=AUDIO,reload_audio=audio.reload,password=os.environ.get('PORTAL_PASSWORD',''),model_name='ACE-Step 1.5 - PT/SDPA',guard_store=guard,run_config_reader=lambda:read_run_config_identity(guard),cancel_generation=cancel,generation_termination_confirmed=termination,cleanup_cancelled_generation=no_cancellation_cleanup,generation_call_state=call_state,require_guard=True,generation_enabled=True,public_preview=False,fixed_generation_duration_seconds=30,archived_run_guards={os.environ.get('SONORA_ARCHIVE_RUN_ID','00000000-0000-4000-8000-000000000001'):(archive_guard,lambda:read_run_config_identity(archive_guard))})
 # No schema/debug routes exposed on owner portal.
 api.router.routes[:]=[r for r in api.router.routes if getattr(r,'path','') not in {'/docs','/redoc','/openapi.json','/docs/oauth2-redirect'}]
 @api.get('/api/shared-studio')
 def shared_status():return manager.available()
 if google_client_id:
  from sonora.google_auth import install_google_auth
  auth_store=modal.Dict.from_name(NAME+'-google-sessions',create_if_missing=True)
  def bind_archive(identity):
   if identity.get('role')=='owner':
    job=os.environ['SONORA_ARCHIVE_JOB_ID']
    record=jobs.get(job,{})
    if not record.get('_owner_sub'):
     record['_owner_sub']=identity['sub'];jobs[job]=record
  install_google_auth(api,on_identity=bind_archive,client_id=google_client_id,origin=os.environ.get('SONORA_ORIGIN','https://studio.example.com'),owner_email=os.environ['SONORA_OWNER_EMAIL'],password=os.environ.get('PORTAL_PASSWORD',''),store=auth_store)
 return api
