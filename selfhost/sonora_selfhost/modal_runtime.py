"""Experimental own-account Modal runtime. Importing this module allocates nothing.

The normal studio stays lightweight. Only explicit CLI prepare/start phases spend
provider resources. This route has not produced a real GPU track in validation.
"""
from __future__ import annotations
import argparse, json, os, pathlib, secrets, subprocess, sys, time, uuid
from .config import ConfigError, Config, atomic_json, save_config, private_dir
from .store import ProcessLock, Store

MOUNT='/sonora-user'
CPU_SECONDS=1200
GPU_SECONDS=1440
PREFIX='sonora-user-'


def runtime_env():
    env={k:v for k,v in os.environ.items() if not k.startswith(('ACESTEP_','MODAL_TOKEN','MODAL_OAUTH','SONORA_API_KEY')) and k not in {'PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','UV_PROJECT_ENVIRONMENT'}}
    env.update({'UV_CACHE_DIR':'/tmp/sonora-uv-cache','UV_LINK_MODE':'copy','HF_HUB_DISABLE_XET':'1','PYTHONUNBUFFERED':'1'})
    return env


def prepare_remote(volume, guard, queue_deadline):
    """Full exact CPU preparation. Does not load a GPU or submit music."""
    import pathlib, subprocess, time, json, shutil, os
    from sonora_selfhost.engine import manifest, check_source, verify_models
    from sonora_selfhost.modal_runtime import runtime_env, MOUNT, CPU_SECONDS
    root=pathlib.Path(MOUNT); engine=root/'home/engine/ACE-Step-1.5'
    if not guard.put('prepare-attempt',time.time(),skip_if_exists=True):raise RuntimeError('Duplicate preparation blocked')
    if time.time()>queue_deadline:raise TimeoutError('Preparation queue window expired')
    deadline=time.time()+CPU_SECONDS-30; env=runtime_env(); spec=manifest()
    def run(stage,argv,cwd=None):
        guard.put('progress',{'stage':stage,'utc':time.time()})
        with (root/'prepare.log').open('a',buffering=1) as log:
            p=subprocess.Popen(argv,cwd=cwd,env=env,stdout=log,stderr=log,start_new_session=True)
            try:p.wait(timeout=max(1,deadline-time.time()))
            finally:
                if p.poll() is None:
                    import signal
                    os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=5)
            if p.returncode:raise RuntimeError('Preparation failed at '+stage+'; inspect prepare.log')
    try:
        engine.parent.mkdir(parents=True,exist_ok=True)
        run('source-clone',['git','clone','--no-checkout',spec['source_repository'],str(engine)])
        run('source-pin',['git','checkout','--detach',spec['source_revision']],engine)
        run('full-frozen-lock',['uv','sync','--frozen','--python','3.12'],engine)
        script='from huggingface_hub import snapshot_download; snapshot_download(repo_id='+repr(spec['model_repository'])+',revision='+repr(spec['model_revision'])+",local_dir='checkpoints',max_workers=1,force_download=True)"
        run('full-model-download',['uv','run','--frozen','--no-sync','python','-I','-c',script],engine)
        check_source(engine,spec);verify_models(engine,spec)
        size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_symlink())
        if size>24*1024**3:raise RuntimeError('Prepared data exceeds24GiB retention bound')
        result={'status':'prepared','gpu_verified':False,'source':spec['source_revision'],'model':spec['model_revision'],'bytes':size}
        (root/'prepared.json').write_text(json.dumps(result));volume.commit();guard.put('prepared',result);return result
    except BaseException:
        # Background/teardown commits exist. Remove models/venv before exit.
        bulk=root/'home'
        if bulk.exists():
            shutil.rmtree(bulk)
        if bulk.exists():raise RuntimeError('Failed preparation bulk removal is unverified; stop/delete session resources immediately')
        (root/'prepared.json').unlink(missing_ok=True)
        log=root/'prepare.log'
        if log.exists():
            with log.open('rb') as f:f.seek(max(0,log.stat().st_size-1024**2));tail=f.read(1024**2)
            log.write_bytes(tail)
        try:volume.commit()
        except Exception:pass
        raise


def serve_remote(volume,guard,key,queue_deadline):
    """A single capped authenticated GPU session, never an automatic music task."""
    import pathlib,time,json,subprocess,os,signal,httpx
    from sonora_selfhost.engine import manifest,check_source,verify_models
    from sonora_selfhost.modal_runtime import runtime_env,MOUNT,GPU_SECONDS
    if not guard.put('serve-attempt',time.time(),skip_if_exists=True):raise RuntimeError('Duplicate serving session blocked')
    if time.time()>queue_deadline:raise TimeoutError('GPU queue window expired')
    import modal
    root=pathlib.Path(MOUNT);volume.reload();spec=manifest();engine=root/'home/engine/ACE-Step-1.5'
    ready=json.loads((root/'prepared.json').read_text());confirmed=guard.get('prepared',None)
    if ready!=confirmed or ready.get('status')!='prepared':raise RuntimeError('Committed preparation is not confirmed')
    deadline=time.time()+GPU_SECONDS-30;env=runtime_env();env['SONORA_HOME']=str(root/'home')
    check_source(engine,spec);verify_models(engine,spec)
    # Run the shipped installer, not a renamed readiness fixture.
    install=subprocess.Popen(['python','-I','-u','-m','sonora_selfhost.cli','engine','install','--accept-downloads'],env=env,start_new_session=True)
    try:install.wait(timeout=max(1,deadline-time.time()))
    finally:
        if install.poll() is None:os.killpg(install.pid,signal.SIGKILL);install.wait(timeout=5)
    if install.returncode:raise RuntimeError('Shipped GPU installer failed')
    env.update({'ACESTEP_API_KEY':key,'ACESTEP_CONFIG_PATH':'acestep-v15-turbo','ACESTEP_INIT_LLM':'false','ACESTEP_NO_INIT':'false','ACESTEP_DEVICE':'cuda','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','ACESTEP_API_HOST':'0.0.0.0','ACESTEP_API_PORT':'8001'})
    with (root/'serve.log').open('a',buffering=1) as log:
        process=subprocess.Popen(['uv','run','--frozen','--no-sync','acestep-api','--host','0.0.0.0','--port','8001'],cwd=engine,env=env,stdout=log,stderr=log,start_new_session=True)
        try:
            with modal.forward(8001) as tunnel:
                with httpx.Client(trust_env=False,timeout=5) as client:
                    while time.time()<deadline:
                        if process.poll() is not None:raise RuntimeError('GPU server exited; inspect serve.log')
                        try:
                            response=client.get('http://127.0.0.1:8001/v1/models',headers={'Authorization':'Bearer '+key})
                            if response.status_code==200 and response.json().get('data',{}).get('models'):break
                        except (httpx.HTTPError,ValueError):pass
                        time.sleep(2)
                    else:raise TimeoutError('GPU readiness window expired')
                guard.put('endpoint',{'url':tunnel.url,'expires_epoch':deadline,'status':'ready','music_verified':False})
                while time.time()<deadline:
                    if process.poll() is not None:raise RuntimeError('GPU server exited')
                    time.sleep(min(2,max(0,deadline-time.time())))
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            guard.put('endpoint',{'status':'stopped'})
    volume.commit();return {'status':'session-ended','music_verified':False}


def load_state(root):
    try:s=json.loads((root/'modal-state.json').read_text())
    except (OSError,ValueError):raise ConfigError('No Modal session state. Run sonora modal plan, then prepare.') from None
    if not isinstance(s.get('namespace'),str) or not s['namespace'].startswith(PREFIX) or len(s['namespace'])!=len(PREFIX)+32:raise ConfigError('Invalid own-account Modal state')
    suffix=s['namespace'][len(PREFIX):]
    if any(c not in '0123456789abcdef' for c in suffix) or s.get('volume')!=s['namespace']+'-models' or s.get('guard')!=s['namespace']+'-state':raise ConfigError('Modal resource names do not match this unique session. Refusing account changes.')
    return s


def app_list():
    try:return json.loads(subprocess.run([sys.executable,'-m','modal','app','list','--json'],capture_output=True,text=True,check=True,timeout=45).stdout)
    except (OSError,ValueError,subprocess.SubprocessError):raise ConfigError('Could not verify current Modal apps. No new work started.') from None


def stopped(apps,ids):
    return all(any(a['app_id']==i and a['state']=='stopped' and int(a['tasks'])==0 for a in apps) for i in ids)


def stop_owned(root,s,modal,delete_models=False):
    errors=[];phases=s['phases'];namespace=s['namespace']
    try:
        for a in app_list():
            if a.get('description','').startswith(namespace) and a['app_id'] not in [p.get('app_id') for p in phases.values()]:phases['orphan-'+a['app_id']]={'app_id':a['app_id']}
    except ConfigError:errors.append('orphan discovery unavailable')
    try:atomic_json(root/'modal-state.json',s)
    except OSError:errors.append('state persistence failed')
    for p in list(phases.values()):
        if p.get('call_id'):
            try:modal.FunctionCall.from_id(p['call_id']).cancel(terminate_containers=True)
            except Exception:errors.append('call cancellation failed')
        if p.get('app_id'):
            try:subprocess.run([sys.executable,'-m','modal','app','stop',p['app_id'],'--yes'],capture_output=True,text=True,check=True,timeout=45)
            except Exception:errors.append('app stop needs readback')
    apps=app_list();ids=[p['app_id'] for p in phases.values() if p.get('app_id')]
    if not stopped(apps,ids) or any(a.get('description','').startswith(namespace) and (a['state']!='stopped' or int(a['tasks'])!=0) for a in apps) or 'orphan discovery unavailable' in errors:raise ConfigError('Stop is unverified. Keep modal-state.json and inspect your Modal dashboard. Do not start again.')
    s['stopped_verified']=True;s['stop_errors']=errors
    if delete_models:
        for typ,name in [(modal.Dict,s['guard']),(modal.Volume,s['volume'])]:
            typ.objects.delete(name,allow_missing=True)
            if name in [x.name for x in typ.objects.list()]:raise ConfigError('Resource deletion not yet verified. Check your Modal storage.')
        s['models_deleted']=True
    atomic_json(root/'modal-state.json',s)
    (root/'modal-session-key.json').unlink(missing_ok=True)
    print('All owned session apps stopped/tasks0. '+('Model storage deleted and absence verified.' if delete_models else 'Model storage remains billable. Use sonora modal stop --delete-models to remove it.'))


def verify_workspace(expected):
    if not expected:raise ConfigError('Supply --workspace YOUR_MODAL_WORKSPACE after checking modal token info. No allocation performed.')
    script="import asyncio; from modal.client import _Client; from modal_proto import api_pb2\nasync def main():\n c=await _Client.from_env();r=await c._stub.TokenInfoGet(api_pb2.TokenInfoGetRequest());print(r.workspace_name)\nasyncio.run(main())"
    try:actual=subprocess.run([sys.executable,'-I','-c',script],capture_output=True,text=True,check=True,timeout=30).stdout.strip()
    except Exception:raise ConfigError('Could not verify Modal workspace. Run sonora modal login and modal token info. No allocation performed.') from None
    if actual!=expected:raise ConfigError('Active Modal workspace does not match --workspace. Switch your Modal profile before continuing. No allocation performed.')
    print('Verified your Modal workspace: '+actual)


def _modal_command(args,root):
    if args.action=='guide':
        print(pathlib.Path(__file__).with_name('modal-setup.md').read_text());return 0
    if args.action=='plan':
        print('EXPERIMENTAL own-account Modal route. Real GPU music is NOT yet validated. No allocation performed.')
        print('Read sonora modal guide for the complete installed instructions.')
        print('Install your downloaded wheel with its [modal] extra as shown by sonora modal guide, then sonora modal login.')
        print('Prepare: sonora modal prepare --workspace YOUR_WORKSPACE --accept-compute --accept-downloads. Full pinned source/lock/models, CPU20min + startup120s + queue10min.')
        print('Start: sonora modal start --workspace YOUR_WORKSPACE --accept-compute. One L4,4CPU,32GiB; GPU24min includes installation/loading/idle/use, plus startup120s + queue10min. No automatic song or retry.')
        print('Image builds and storage can cost extra. Provider rates/credits/taxes vary. Runtime limits are NOT a guaranteed dollar cap. Review your own Modal billing limits first.')
        print('After status reports ready: sonora modal connect, then sonora start; explicitly Generate in the studio. Modal credentials stay with its SDK, never the app owner.')
        print('Stop: sonora modal stop --delete-models. Closing the studio does not stop cloud resources. Storage is billable until deletion; billing may lag.')
        return 0
    try:import modal
    except ImportError:raise ConfigError('Install this downloaded distribution with the [modal] extra first. No account resources were created.') from None
    if args.action=='login':return subprocess.run([sys.executable,'-m','modal','token','new']).returncode
    private_dir(root)
    if args.action in {'prepare','start'} and not args.accept_compute:raise ConfigError('Read sonora modal plan, review your own account billing limits, then add --accept-compute.')
    if args.action=='prepare' and not args.accept_downloads:raise ConfigError('Add --accept-downloads after reading the pinned code/model terms in sonora engine plan.')
    if args.action in {'prepare','start'}:verify_workspace(args.workspace)
    lock=ProcessLock(root)
    try:
        if args.action=='prepare':
            if (root/'modal-state.json').exists():raise ConfigError('Existing Modal state found. Inspect/status/stop it; Sonora never replaces an uncertain session.')
            namespace=PREFIX+uuid.uuid4().hex;s={'namespace':namespace,'workspace':args.workspace,'volume':namespace+'-models','guard':namespace+'-state','phases':{}}
            atomic_json(root/'modal-state.json',s)
            volume=modal.Volume.from_name(s['volume'],create_if_missing=True);guard=modal.Dict.from_name(s['guard'],create_if_missing=True)
        else:
            s=load_state(root)
            verify_workspace(s.get('workspace'))
            if args.action=='start' and args.workspace!=s.get('workspace'):raise ConfigError('Session belongs to a different workspace. No new work started.')
            volume=modal.Volume.from_name(s['volume']);guard=modal.Dict.from_name(s['guard'])
        if args.action=='stop':stop_owned(root,s,modal,args.delete_models);return 0
        if args.action=='reset':
            if not s.get('stopped_verified') or not s.get('models_deleted'):raise ConfigError('First run sonora modal stop --delete-models and verify cleanup.')
            if not stopped(app_list(),[p['app_id'] for p in s['phases'].values() if p.get('app_id')]):raise ConfigError('Current app termination is not verified; state retained.')
            if s['volume'] in [v.name for v in modal.Volume.objects.list()] or s['guard'] in [g.name for g in modal.Dict.objects.list()]:raise ConfigError('Resource deletion is not verified; state retained.')
            archive=root/'modal-history';private_dir(archive)
            atomic_json(archive/(s['namespace']+'.json'),s)
            (root/'modal-state.json').unlink();(root/'modal-session-key.json').unlink(missing_ok=True)
            print('Stopped session archived. Music library unchanged. A new prepare is now an explicit new compute decision.');return 0
        if args.action in {'status','connect'}:
            if s.get('stopped_verified'):
                print('Session stopped and verified at last stop. '+('Model storage deleted.' if s.get('models_deleted') else 'Model storage retained and may remain billable.'))
                if args.action=='connect':raise ConfigError('Session was stopped. Endpoint is no longer usable.')
                return 0
            # Read completion before considering expiry. Deadlines bound active work,
            # never successful historical preparation or a finished session.
            for name,phase in s['phases'].items():
                if not phase.get('call_id') or phase.get('completed_status'):continue
                try:
                    result=modal.FunctionCall.from_id(phase['call_id']).get(timeout=0)
                    expected='prepared' if name=='prepare' else 'session-ended'
                    if not isinstance(result,dict) or result.get('status')!=expected:raise RuntimeError('Unexpected phase result')
                    phase['completed_status']=expected
                    atomic_json(root/'modal-state.json',s)
                    print(name,expected)
                    continue
                except (TimeoutError,modal.exception.TimeoutError):pass
                except Exception:
                    stop_owned(root,s,modal,delete_models=True)
                    raise ConfigError(name+' failed. All owned apps stopped and storage removed. Inspect provider logs; do not retry blindly.') from None
                attempt='prepare-attempt' if name=='prepare' else 'serve-attempt'
                if time.time()>phase.get('absolute_deadline',float('inf')) or (time.time()>phase.get('queue_deadline',float('inf')) and guard.get(attempt,None) is None):
                    stop_owned(root,s,modal,delete_models=True)
                    raise ConfigError('Active phase deadline expired. All session resources stopped/deleted, no retry.')
                print(name,'pending/running')
            endpoint=guard.get('endpoint',None)
            if s['phases'].get('serve',{}).get('completed_status'):endpoint=None
            print('Cloud endpoint:',endpoint.get('status') if endpoint else 'not ready')
            if args.action=='connect':
                if not endpoint or endpoint.get('status')!='ready' or endpoint.get('expires_epoch',0)<=time.time():raise ConfigError('Modal endpoint is not ready or session expired.')
                from .provider import AceStep
                key=json.loads((root/'modal-session-key.json').read_text())['key'];provider=AceStep(Config(endpoint['url'],'acestep-v15-turbo'),key)
                try:models=provider.models()
                finally:provider.close()
                if any(j['status'] not in {'complete','failed','acknowledged'} for j in Store(root).all()):raise ConfigError('Reconcile existing active jobs before switching endpoint.')
                save_config(root,Config(endpoint['url'],models[0]),key)
                print('Own-account endpoint connected. Model discovery only, not music proof. Run sonora start; closing it does not stop Modal.')
            return 0
        phase='prepare' if args.action=='prepare' else 'serve'
        if phase in s['phases']:raise ConfigError('This phase was already submitted. Use status/stop, never duplicate it.')
        volume.hydrate();guard.hydrate()
        if phase=='serve':
            previous=s['phases'].get('prepare',{})
            if not previous.get('call_id') or modal.FunctionCall.from_id(previous['call_id']).get(timeout=0).get('status')!='prepared':raise ConfigError('Full CPU preparation must complete first.')
            previous['completed_status']='prepared';atomic_json(root/'modal-state.json',s)
            subprocess.run([sys.executable,'-m','modal','app','stop',previous['app_id'],'--yes'],check=True,timeout=45)
            if not stopped(app_list(),[previous['app_id']]):raise ConfigError('CPU app termination is not verified.')
            key=secrets.token_urlsafe(32);atomic_json(root/'modal-session-key.json',{'key':key})
        # SDK authentication is the downloader's, never copied from a hosted app.
        print('Using your active Modal SDK account. Check modal token info to confirm its workspace before accepting compute.')
        from modal._vendor.cloudpickle import register_pickle_by_value
        register_pickle_by_value(sys.modules[__name__])
        image=modal.Image.debian_slim(python_version='3.12').apt_install('git','ffmpeg','ca-certificates').pip_install('uv==0.13.5','fastapi==0.162.5','httpx==0.28.1','uvicorn==0.54.0').add_local_dir(str(pathlib.Path(__file__).parent),'/opt/sonora-selfhost-package',copy=True,ignore=['__pycache__','*.pyc']).run_commands("python -c \"import shutil,sysconfig,pathlib; shutil.copytree('/opt/sonora-selfhost-package',pathlib.Path(sysconfig.get_paths()['purelib'])/'sonora_selfhost',dirs_exist_ok=True)\"")
        app=modal.App(s['namespace']+'-'+phase,include_source=False);duration=CPU_SECONDS if phase=='prepare' else GPU_SECONDS
        fn=app.function(image=image,volumes={MOUNT:volume},gpu=None if phase=='prepare' else 'L4',cpu=(2,2) if phase=='prepare' else (4,4),memory=(8192,8192) if phase=='prepare' else (32768,32768),timeout=duration,startup_timeout=120,max_containers=1,min_containers=0,buffer_containers=0,scaledown_window=2,retries=0,single_use_containers=True,serialized=True,include_source=False)(prepare_remote if phase=='prepare' else serve_remote)
        queue_deadline=time.time()+600;s['phases'][phase]={'queue_deadline':queue_deadline,'absolute_deadline':queue_deadline+120+duration}
        atomic_json(root/'modal-state.json',s)
        try:
            with modal.enable_output(),app.run(detach=True):
                s['phases'][phase]['app_id']=app.app_id;atomic_json(root/'modal-state.json',s)
                call=fn.spawn(volume,guard,queue_deadline) if phase=='prepare' else fn.spawn(volume,guard,key,queue_deadline)
                s['phases'][phase]['call_id']=call.object_id;atomic_json(root/'modal-state.json',s)
        except BaseException:
            stop_owned(root,s,modal,delete_models=True);raise
        print('One '+phase+' call submitted. Use sonora modal status. No music generated automatically. Stop with sonora modal stop --delete-models.')
        return 0
    finally:lock.close()


def modal_command(args,root):
    try:return _modal_command(args,root)
    except (ConfigError,KeyboardInterrupt,EOFError):raise
    except Exception as exc:
        # SDK transport/provider messages can include sensitive input. Keep
        # durable handles, not a raw exception string, for manual reconciliation.
        raise ConfigError('Modal operation failed ('+type(exc).__name__+'). Keep modal-state.json; inspect your own Modal dashboard before new work. No automatic retry.') from None
