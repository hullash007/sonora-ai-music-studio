from __future__ import annotations
import io
import json
import os
import tempfile
import threading
import unittest
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from sonora_selfhost.config import Config, ConfigError, endpoint_url, load_config, read_key, save_config
from sonora_selfhost.provider import AceStep, ProviderError, SubmissionUnknown, validate_wav
from sonora_selfhost.server import create_app
from sonora_selfhost.store import BusyError, ProcessLock, Store, Worker


def wav_bytes(seconds=10):
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(8000)
        f.writeframes(b'\x00\x00' * (8000 * seconds))
    return out.getvalue()


def wrap(data):
    return httpx.Response(200, json={'data': data, 'code':200, 'error':None})

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=Config('http://127.0.0.1:8001','acestep-v15-turbo')

    def provider(self, handler, key=''):
        result=AceStep(self.config,key,transport=httpx.MockTransport(handler))
        self.addCleanup(result.close)
        return result

    def store_job(self,store):
        return store.create(str(uuid.uuid4()),'Warm piano','',10,self.config.model,self.config.endpoint)

class ConfigTests(Base):
    def test_no_config(self):
        with self.assertRaises(ConfigError):load_config(self.root)
    def test_roundtrip_key_separate(self):
        save_config(self.root,self.config,'test-private-key')
        loaded=load_config(self.root)
        self.assertNotIn('test-private-key',(self.root/'config.json').read_text())
        self.assertEqual(read_key(self.root,loaded),'test-private-key')
        if os.name!='nt':
            self.assertEqual(self.root.stat().st_mode&0o777,0o700)
            self.assertEqual(next(self.root.glob('credential-*')).stat().st_mode&0o777,0o600)
    def test_rotation_removes_old_key(self):
        save_config(self.root,self.config,'one');save_config(self.root,self.config,'two')
        self.assertEqual(len(list(self.root.glob('credential-*'))),1)
        self.assertEqual(read_key(self.root,load_config(self.root)),'two')
    def test_no_key_removes_stale_credential(self):
        save_config(self.root,self.config,'one');save_config(self.root,self.config,None)
        self.assertFalse(list(self.root.glob('credential-*')))
    def test_environment_override(self):
        save_config(self.root,self.config,None)
        with patch.dict(os.environ,{'SONORA_API_KEY':'test-env'}):self.assertEqual(read_key(self.root,load_config(self.root)),'test-env')
    def test_remote_https(self):self.assertEqual(endpoint_url('https://example.test/'),'https://example.test')
    def test_loopback_http(self):
        for u in ['http://127.0.0.1:8001','http://localhost:8001','http://[::1]:8001']:
            self.assertEqual(endpoint_url(u),u)
    def test_bad_endpoints(self):
        for u in ['http://example.test','https://user:key@example.test','https://example.test/api','https://example.test?key=oops','https://example.test/#key','file:///tmp/audio','https://example.test:99999','https://exa mple.test']:
            with self.subTest(u=u),self.assertRaises(ConfigError):endpoint_url(u)
    def test_key_newline_rejected(self):
        with self.assertRaises(ConfigError):save_config(self.root,self.config,'one\ntwo')
    def test_failed_atomic_write_preserves_active(self):
        save_config(self.root,self.config,'one'); original=(self.root/'config.json').read_bytes()
        import sonora_selfhost.config as module
        original_atomic=module.atomic_json
        def fail(path,value):
            if path.name=='config.json':raise OSError('test full disk')
            original_atomic(path,value)
        with patch.object(module,'atomic_json',side_effect=fail),self.assertRaises(OSError):save_config(self.root,self.config,'two')
        self.assertEqual((self.root/'config.json').read_bytes(),original)
        self.assertEqual(read_key(self.root,load_config(self.root)),'one')

class ProtocolTests(Base):
    def test_models_wrapper_and_loaded_filter(self):
        provider=self.provider(lambda r:wrap({'models':[{'name':'loaded','is_loaded':True},{'name':'not-loaded','is_loaded':False}]}))
        self.assertEqual(provider.models(),['loaded'])
    def test_models_empty(self):
        p=self.provider(lambda r:wrap({'models':[]}))
        with self.assertRaises(ProviderError):p.models()
    def test_models_malformed(self):
        for data in [None,[],{}, {'models':[None]}]:
            p=self.provider(lambda r:wrap(data))
            with self.assertRaises(ProviderError):p.models()
    def test_submit_exactly_one_and_auth_not_body(self):
        seen=[]
        def handle(r):
            seen.append(r);return wrap({'task_id':'remote-1'})
        p=self.provider(handle,'private-test-key')
        self.assertEqual(p.submit('Piano','',10,'loaded'),'remote-1')
        self.assertEqual(len(seen),1);body=json.loads(seen[0].content)
        self.assertEqual(body['batch_size'],1);self.assertEqual(body['audio_format'],'wav')
        self.assertFalse(body['thinking']);self.assertFalse(body['use_cot_caption'])
        self.assertNotIn('private-test-key',seen[0].content.decode())
        self.assertEqual(seen[0].headers['Authorization'],'Bearer private-test-key')
    def test_submit_timeout_is_uncertain_without_retry(self):
        seen=[]
        def handle(r):seen.append(r);raise httpx.ReadTimeout('private-test-key')
        p=self.provider(handle)
        with self.assertRaises(SubmissionUnknown) as exc:p.submit('Piano','',10,'loaded')
        self.assertNotIn('private-test-key',str(exc.exception));self.assertEqual(len(seen),1)
    def test_submit_http_error_no_body_disclosure(self):
        for status in [401,429,500,502,307]:
            p=self.provider(lambda r:httpx.Response(status,text='private-test-key'))
            with self.assertRaises(SubmissionUnknown) as exc:p.submit('Piano','',10,'loaded')
            self.assertNotIn('private-test-key',str(exc.exception))
    def test_submit_missing_id_uncertain(self):
        p=self.provider(lambda r:wrap({}))
        with self.assertRaises(SubmissionUnknown):p.submit('Piano','',10,'loaded')
    def test_poll_success_real_schema(self):
        row={'task_id':'r1','status':1,'result':json.dumps([{'file':'/v1/audio?path=%2Ftmp%2Foutput.wav','status':1}])}
        p=self.provider(lambda r:wrap([row]));self.assertEqual(p.poll('r1')[0],'complete')
    def test_poll_running_failed(self):
        for value,name in [(0,'running'),(2,'failed')]:
            p=self.provider(lambda r:wrap([{'task_id':'r1','status':value}]))
            self.assertEqual(p.poll('r1'),(name,None))
    def test_poll_other_task_and_bad_status(self):
        for rows in [[],[{'task_id':'other','status':1}],[{'task_id':'r1','status':True}],[{'task_id':'r1','status':3}]]:
            p=self.provider(lambda r:wrap(rows))
            with self.assertRaises(ProviderError):p.poll('r1')
    def test_multioutput_rejected(self):
        p=self.provider(lambda r:wrap([{'task_id':'r1','status':1,'result':json.dumps([{},{}])}]))
        with self.assertRaises(ProviderError):p.poll('r1')
    def test_no_external_audio_or_bad_route(self):
        p=self.provider(lambda r:wrap({}))
        for value in ['https://evil.test/v1/audio?path=a.wav','//evil.test/v1/audio?path=a.wav','/other?path=a.wav','/v1/audio?path=a.wav&key=oops','/v1/audio?path=a.mp3','/v1/audio?path=a.wav#fragment','http://u:p@127.0.0.1:8001/v1/audio?path=a.wav']:
            with self.subTest(value=value),self.assertRaises(ProviderError):p.audio_url(value)
    def test_download_valid_pcm(self):
        p=self.provider(lambda r:httpx.Response(200,content=wav_bytes(),headers={'content-type':'audio/wav'}))
        dest=self.root/'saved.wav';self.assertEqual(p.download('/v1/audio?path=x.wav',dest,10),10)
        self.assertTrue(dest.is_file());self.assertFalse(dest.with_suffix('.partial').exists())
    def test_audio_redirect_not_followed(self):
        seen=[]
        def handle(r):seen.append(r);return httpx.Response(302,headers={'location':'https://evil.test'})
        p=self.provider(handle,'private-test-key')
        with self.assertRaises(ProviderError):p.download('/v1/audio?path=x.wav',self.root/'x.wav',10)
        self.assertEqual(len(seen),1);self.assertFalse((self.root/'x.wav').exists())
    def test_wrong_media_not_published(self):
        p=self.provider(lambda r:httpx.Response(200,text='<html>wrong</html>',headers={'content-type':'text/html'}))
        with self.assertRaises(ProviderError):p.download('/v1/audio?path=x.wav',self.root/'x.wav',10)
        self.assertFalse((self.root/'x.wav').exists())
    def test_wav_truncation_and_duration(self):
        for data in [b'not-wav',wav_bytes()[:80],wav_bytes(2)]:
            (self.root/'x.wav').write_bytes(data)
            with self.assertRaises(ProviderError):validate_wav(self.root/'x.wav',10)
    def test_size_limit(self):
        p=self.provider(lambda r:httpx.Response(200,content=wav_bytes(),headers={'content-type':'audio/wav'}))
        with patch('sonora_selfhost.provider.MAX_AUDIO',100),self.assertRaises(ProviderError):p.download('/v1/audio?path=x.wav',self.root/'x.wav',10)
        self.assertFalse((self.root/'x.partial').exists())
    def test_json_size_limit(self):
        p=self.provider(lambda r:wrap({'models':[{'name':'a'*1000}]}))
        with patch('sonora_selfhost.provider.MAX_JSON',100),self.assertRaises(ProviderError):p.models()

class DurableTests(Base):
    def test_idempotency_and_changed_input(self):
        store=Store(self.root);key=str(uuid.uuid4())
        a=store.create(key,'Piano','',10,'model',self.config.endpoint)
        self.assertEqual(store.create(key,'Piano','',10,'model',self.config.endpoint),a)
        with self.assertRaises(ValueError):store.create(key,'Other','',10,'model',self.config.endpoint)
    def test_concurrent_last_slot(self):
        store=Store(self.root)
        def create(n):
            try:return self.store_job(store)
            except BusyError:return None
        with ThreadPoolExecutor(max_workers=8) as pool:result=list(pool.map(create,range(8)))
        self.assertEqual(sum(bool(x) for x in result),1)
    def test_restart_submission_not_retried(self):
        store=Store(self.root);jid=self.store_job(store)
        store.update(jid,expected=('queued',),status='submitting')
        store=Store(self.root);store.recover()
        self.assertEqual(store.get(jid)['status'],'uncertain')
        with self.assertRaises(BusyError):self.store_job(store)
    def test_terminal_monotonic(self):
        store=Store(self.root);jid=self.store_job(store)
        store.update(jid,expected=('queued',),status='complete')
        self.assertFalse(store.update(jid,expected=('remote',),status='failed'))
        self.assertEqual(store.get(jid)['status'],'complete')
    def test_process_lock(self):
        lock=ProcessLock(self.root)
        try:
            with self.assertRaises(BusyError):ProcessLock(self.root)
        finally:lock.close()
        ProcessLock(self.root).close()
    def test_audio_path_traversal(self):
        with self.assertRaises(ValueError):Store(self.root).audio_path('../outside')
    def test_successful_worker_and_restart_retrieval(self):
        calls=[]
        def handle(r):
            calls.append(r.url.path)
            if r.url.path=='/v1/models':return wrap({'models':[{'name':self.config.model}]})
            if r.url.path=='/release_task':return wrap({'task_id':'r1'})
            if r.url.path=='/query_result':return wrap([{'task_id':'r1','status':1,'result':json.dumps([{'status':1,'file':'/v1/audio?path=x.wav'}])}])
            return httpx.Response(200,content=wav_bytes(),headers={'content-type':'audio/wav'})
        p=self.provider(handle);store=Store(self.root);jid=self.store_job(store)
        Worker(store,p,poll_seconds=.001).process(jid)
        self.assertEqual(Store(self.root).get(jid)['status'],'complete')
        self.assertEqual(calls.count('/release_task'),1)
        # A new process tracks an already accepted task without another submit.
        jid2=self.store_job(store);store.update(jid2,expected=('queued',),status='remote',remote_id='r1')
        Worker(Store(self.root),p,poll_seconds=.001).process(jid2)
        self.assertEqual(calls.count('/release_task'),1)
    def test_unknown_worker_blocks_without_resubmit(self):
        calls=[]
        def handle(r):
            calls.append(r.url.path)
            if r.url.path=='/v1/models':return wrap({'models':[{'name':self.config.model}]})
            raise httpx.ReadTimeout('private-test-key')
        p=self.provider(handle);store=Store(self.root);jid=self.store_job(store);worker=Worker(store,p)
        worker.process(jid);worker.process(jid)
        self.assertEqual(store.get(jid)['status'],'uncertain');self.assertEqual(calls.count('/release_task'),1)
    def test_invalid_model_never_submitted(self):
        calls=[]
        def handle(r):calls.append(r.url.path);return wrap({'models':[{'name':'another-model'}]})
        p=self.provider(handle);store=Store(self.root);jid=self.store_job(store)
        Worker(store,p).process(jid)
        self.assertEqual(store.get(jid)['status'],'failed');self.assertNotIn('/release_task',calls)
    def test_retrieval_timeout_does_not_resubmit(self):
        calls=[]
        def handle(r):calls.append(r.url.path);raise httpx.ReadTimeout('test')
        p=self.provider(handle);store=Store(self.root);jid=self.store_job(store)
        store.update(jid,expected=('queued',),status='remote',remote_id='r1')
        Worker(store,p,poll_seconds=.001,max_poll_seconds=.003).process(jid)
        self.assertEqual(store.get(jid)['status'],'retrieval_error');self.assertNotIn('/release_task',calls)

class ServerTests(Base):
    def client(self):
        store=Store(self.root)
        p=self.provider(lambda r:wrap({'models':[{'name':self.config.model}]}),'private-test-key')
        app=create_app(store,p,token='test-session',run_worker=False)
        client=TestClient(app,base_url='http://127.0.0.1:8787');self.addCleanup(client.close)
        return client,store
    def login(self,client):
        self.assertEqual(client.post('/api/session',headers={'x-sonora-session':'test-session'}).status_code,200)
    def test_root_without_secret(self):
        c,s=self.client();r=c.get('/')
        self.assertEqual(r.status_code,200);self.assertNotIn('private-test-key',r.text)
        self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
    def test_auth_required(self):
        c,s=self.client()
        for p in ['/api/status','/api/models','/api/jobs']:self.assertEqual(c.get(p).status_code,401)
    def test_session_and_key_never_exposed(self):
        c,s=self.client();self.login(c)
        for p in ['/api/status','/api/models','/api/jobs']:
            response=c.get(p);self.assertEqual(response.status_code,200);self.assertNotIn('private-test-key',response.text)
    def test_cross_origin_and_rebinding(self):
        c,s=self.client();self.login(c)
        self.assertEqual(c.post('/api/session',headers={'Origin':'https://evil.test','x-sonora-session':'test-session'}).status_code,403)
        self.assertEqual(c.get('/',headers={'Host':'evil.test'}).status_code,400)
    def test_generation_requires_confirmation(self):
        c,s=self.client();self.login(c)
        body={'request_key':str(uuid.uuid4()),'prompt':'Piano','model':self.config.model,'duration':10}
        self.assertEqual(c.post('/api/generate',json=body).status_code,400)
        body['confirm_cost']=True
        a=c.post('/api/generate',json=body);b=c.post('/api/generate',json=body)
        self.assertEqual(a.status_code,202);self.assertEqual(a.json(),b.json());self.assertEqual(len(s.all()),1)
    def test_duration_type_and_range(self):
        c,s=self.client();self.login(c)
        for duration in [True,9,121,12.5,'10']:
            body={'request_key':str(uuid.uuid4()),'prompt':'Piano','model':self.config.model,'duration':duration,'confirm_cost':True}
            self.assertEqual(c.post('/api/generate',json=body).status_code,422)
    def test_api_busy(self):
        c,s=self.client();self.login(c)
        body={'request_key':str(uuid.uuid4()),'prompt':'Piano','model':self.config.model,'duration':10,'confirm_cost':True}
        self.assertEqual(c.post('/api/generate',json=body).status_code,202)
        body['request_key']=str(uuid.uuid4());self.assertEqual(c.post('/api/generate',json=body).status_code,409)
    def test_audio_saved_only(self):
        c,s=self.client();self.login(c);jid=self.store_job(s)
        self.assertEqual(c.get('/api/audio/'+jid).status_code,404)
        s.audio_path(jid).write_bytes(wav_bytes());s.update(jid,expected=('queued',),status='complete')
        response=c.get('/api/audio/'+jid)
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Content-Type'],'audio/wav')
        self.assertEqual(response.content,wav_bytes())

if __name__=='__main__':unittest.main()

class EngineTests(Base):
    def test_lockfile_complete_pins(self):
        from sonora_selfhost.engine import manifest
        spec=manifest()
        self.assertEqual(len(spec['source_revision']),40)
        self.assertEqual(len(spec['model_revision']),40)
        self.assertEqual(spec['model_bytes'],sum(x['size'] for x in spec['models']))
        self.assertGreater(len(spec['models']),20)
        for item in spec['models']:
            self.assertNotIn('..',Path(item['path']).parts)
            self.assertFalse(Path(item['path']).is_absolute())
            self.assertTrue('sha256' in item or 'git_blob_sha1' in item)
    def test_checkpoint_hashes_and_corruption(self):
        import hashlib
        from sonora_selfhost.engine import verify_models
        checkpoints=self.root/'checkpoints';checkpoints.mkdir()
        (checkpoints/'small.json').write_bytes(b'hello')
        (checkpoints/'big.bin').write_bytes(b'weights')
        spec={'models':[{'path':'small.json','size':5,'git_blob_sha1':hashlib.sha1(b'blob 5\0hello').hexdigest()},
                        {'path':'big.bin','size':7,'sha256':hashlib.sha256(b'weights').hexdigest()}]}
        verify_models(self.root,spec)
        (checkpoints/'small.json').write_bytes(b'other')
        with self.assertRaises(ConfigError):verify_models(self.root,spec)
    def test_checkpoint_symlink_rejected(self):
        from sonora_selfhost.engine import verify_models
        if os.name=='nt':self.skipTest('POSIX symlink test')
        (self.root/'checkpoints').mkdir();(self.root/'target').write_bytes(b'x')
        (self.root/'checkpoints'/'x').symlink_to(self.root/'target')
        with self.assertRaises(ConfigError):verify_models(self.root,{'models':[{'path':'x','size':1}]})
    def test_engine_plan_has_no_effects(self):
        from argparse import Namespace
        from sonora_selfhost.engine import engine_command
        with patch('subprocess.run') as run,patch('builtins.print'):
            self.assertEqual(engine_command(Namespace(action='plan'),self.root/'absent'),0)
        run.assert_not_called();self.assertFalse((self.root/'absent').exists())
    def test_engine_requires_explicit_downloads(self):
        from argparse import Namespace
        from sonora_selfhost.engine import engine_command
        with patch('sonora_selfhost.engine.check_tools'),self.assertRaises(ConfigError):
            engine_command(Namespace(action='install',accept_downloads=False),self.root)
    def test_engine_wrong_platform_stops(self):
        from sonora_selfhost.engine import check_tools,manifest
        with patch('platform.system',return_value='Darwin'),self.assertRaises(ConfigError):check_tools(manifest())
    def test_engine_start_uses_offline_loopback_no_owner_settings(self):
        from argparse import Namespace
        from sonora_selfhost.engine import engine_command
        spec=None
        with patch('sonora_selfhost.engine.check_tools'),patch('sonora_selfhost.engine.check_source'),patch('sonora_selfhost.engine.verify_models'),patch('sonora_selfhost.engine.run') as run,patch('builtins.print'),patch.dict(os.environ,{'ACESTEP_API_KEY':'old-private-key','ACESTEP_API_HOST':'0.0.0.0','ACESTEP_CONFIG_PATH2':'owner-model'}):
            engine_command(Namespace(action='start'),self.root)
        call=run.call_args
        self.assertEqual(call.kwargs['env']['ACESTEP_API_HOST'],'127.0.0.1')
        self.assertEqual(call.kwargs['env']['HF_HUB_OFFLINE'],'1')
        self.assertNotIn('ACESTEP_API_KEY',call.kwargs['env'])
        self.assertNotIn('ACESTEP_CONFIG_PATH2',call.kwargs['env'])
        self.assertEqual(call.args[0][-4:],['--host','127.0.0.1','--port','8001'])
    def test_engine_frozen_install_commands_and_pinned_model(self):
        from argparse import Namespace
        from sonora_selfhost.engine import engine_command,manifest
        spec=manifest()
        class Disk:free=100*1024**3
        with patch('sonora_selfhost.engine.check_tools'),patch('sonora_selfhost.engine.check_source'),patch('sonora_selfhost.engine.verify_models'),patch('sonora_selfhost.engine.run') as run,patch('shutil.disk_usage',return_value=Disk()),patch('builtins.print'):
            engine_command(Namespace(action='install',accept_downloads=True),self.root)
        cmds=[call.args[0] for call in run.call_args_list]
        self.assertIn(['uv','sync','--frozen','--python','3.12'],cmds)
        self.assertTrue(any(spec['source_revision'] in c for c in cmds))
        self.assertIn(spec['model_revision'],cmds[-1][-1])
        self.assertFalse(json.loads((self.root/'engine'/'installed.json').read_text())['gpu_inference_verified'])

class AdditionalTests(Base):
    def test_failed_download_pauses_without_repeated_downloads(self):
        seen=[]
        def handle(r):
            seen.append(r.url.path)
            if r.url.path=='/query_result':return wrap([{'task_id':'r1','status':1,'result':json.dumps([{'status':1,'file':'/v1/audio?path=x.wav'}])}])
            return httpx.Response(200,content=b'invalid media',headers={'content-type':'audio/wav'})
        store=Store(self.root);jid=self.store_job(store)
        store.update(jid,expected=('queued',),status='remote',remote_id='r1')
        p=self.provider(handle);worker=Worker(store,p,poll_seconds=.001,max_poll_seconds=.01)
        worker.process(jid);worker.process(jid)
        self.assertEqual(store.get(jid)['status'],'retrieval_error')
        self.assertEqual(seen.count('/v1/audio'),1)
    def test_model_task_capability_filter(self):
        p=self.provider(lambda r:wrap({'models':[{'name':'music','supported_task_types':['text2music']},{'name':'only-stems','supported_task_types':['extract']}]}))
        self.assertEqual(p.models(),['music'])
    def test_setup_failed_discovery_keeps_previous(self):
        from sonora_selfhost.cli import main
        save_config(self.root,self.config,'original-private-key')
        original=(self.root/'config.json').read_bytes()
        with patch.dict(os.environ,{'SONORA_HOME':str(self.root)},clear=True),patch('sonora_selfhost.cli.AceStep') as mock,patch('sys.stderr',new=io.StringIO()):
            mock.return_value.models.side_effect=ProviderError('Connection refused')
            self.assertEqual(main(['setup','--endpoint','https://another.test','--no-key']),1)
        self.assertEqual((self.root/'config.json').read_bytes(),original)
        self.assertEqual(read_key(self.root,load_config(self.root)),'original-private-key')
    def test_setup_preserves_config_while_studio_running(self):
        from sonora_selfhost.cli import main
        save_config(self.root,self.config,'original-private-key');original=(self.root/'config.json').read_bytes()
        lock=ProcessLock(self.root)
        try:
            with patch.dict(os.environ,{'SONORA_HOME':str(self.root)},clear=True),patch('sonora_selfhost.cli.AceStep') as mock,patch('builtins.print'),patch('sys.stderr',new=io.StringIO()):
                mock.return_value.models.return_value=['new-model']
                self.assertEqual(main(['setup','--endpoint','https://another.test','--model','new-model','--no-key']),1)
        finally:lock.close()
        self.assertEqual((self.root/'config.json').read_bytes(),original)
