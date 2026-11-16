import importlib, json, os, pathlib, sys, tempfile, types
from unittest.mock import Mock,patch
import pytest
from sonora_selfhost.modal_runtime import runtime_env,load_state,stopped,stop_owned,modal_command
from sonora_selfhost.config import ConfigError

def args(action,**kw):return types.SimpleNamespace(action=action,accept_compute=False,accept_downloads=False,delete_models=False,**kw)

def test_modal_plan_never_imports_sdk_or_mutates_disk(capsys,tmp_path):
    with patch.dict(sys.modules,{'modal':None}):assert modal_command(args('plan'),tmp_path)==0
    assert not list(tmp_path.iterdir());assert 'NOT yet validated' in capsys.readouterr().out

def test_secrets_and_hosted_env_not_in_remote_child():
    with patch.dict(os.environ,{'ACESTEP_API_KEY':'old','MODAL_TOKEN_SECRET':'secret','SONORA_API_KEY':'key','VIRTUAL_ENV':'x','PATH':'/usr/bin'},clear=True):
        env=runtime_env()
    assert not any(k in env for k in ['ACESTEP_API_KEY','MODAL_TOKEN_SECRET','SONORA_API_KEY','VIRTUAL_ENV'])
    assert env['UV_CACHE_DIR']=='/tmp/sonora-uv-cache';assert env['UV_LINK_MODE']=='copy'

def test_state_requires_owned_namespace(tmp_path):
    (tmp_path/'modal-state.json').write_text('{"namespace":"ace-step-15-api"}')
    with pytest.raises(ConfigError):load_state(tmp_path)

def test_stop_requires_all_apps_not_only_cpu():
    assert not stopped([{'app_id':'cpu','state':'stopped','tasks':0}],['cpu','gpu'])
    assert stopped([{'app_id':x,'state':'stopped','tasks':'0'} for x in ['cpu','gpu']],['cpu','gpu'])

def test_stop_attempts_every_app_when_state_disk_full(tmp_path):
    import sonora_selfhost.modal_runtime as m
    s={'namespace':'sonora-user-'+'a'*32,'volume':'vol','guard':'guard','phases':{'prepare':{'app_id':'cpu','call_id':'c'},'serve':{'app_id':'gpu','call_id':'g'}}}
    apps=[{'app_id':x,'description':s['namespace'],'state':'stopped','tasks':0} for x in ['cpu','gpu','orphan']]
    modal=Mock();modal.FunctionCall.from_id.return_value.cancel.side_effect=RuntimeError('RPCfailed')
    with patch.object(m,'app_list',return_value=apps),patch.object(m,'atomic_json',side_effect=OSError('full')),patch.object(m.subprocess,'run') as run:
        with pytest.raises(OSError):stop_owned(tmp_path,s,modal)
    assert {c.args[0][-2] for c in run.call_args_list}=={'cpu','gpu','orphan'}

def test_running_app_blocks_model_deletion(tmp_path):
    import sonora_selfhost.modal_runtime as m
    s={'namespace':'sonora-user-'+'a'*32,'volume':'vol','guard':'guard','phases':{'serve':{'app_id':'gpu'}}};modal=Mock()
    with patch.object(m,'app_list',return_value=[{'app_id':'gpu','description':s['namespace'],'state':'running','tasks':1}]),patch.object(m.subprocess,'run'):
        with pytest.raises(ConfigError):stop_owned(tmp_path,s,modal,True)
    modal.Volume.objects.delete.assert_not_called();modal.Dict.objects.delete.assert_not_called()

def test_cpu_duplicate_guard_prevents_work():
    from sonora_selfhost.modal_runtime import prepare_remote
    guard=Mock();guard.put.return_value=False;volume=Mock()
    with pytest.raises(RuntimeError,match='Duplicate'):prepare_remote(volume,guard,0)
    volume.commit.assert_not_called()

def test_gpu_duplicate_guard_prevents_work():
    from sonora_selfhost.modal_runtime import serve_remote
    guard=Mock();guard.put.return_value=False;volume=Mock()
    with pytest.raises(RuntimeError,match='Duplicate'):serve_remote(volume,guard,'notreal',0)
    volume.reload.assert_not_called()

def test_cli_parses_own_account_actions():
    from sonora_selfhost.cli import parser
    a=parser().parse_args(['modal','prepare','--accept-compute','--accept-downloads'])
    assert a.accept_compute and a.accept_downloads

def test_wrong_workspace_fails_before_allocation():
    import sonora_selfhost.modal_runtime as m
    with patch.object(m.subprocess,'run',return_value=types.SimpleNamespace(stdout='different\n')):
        with pytest.raises(ConfigError,match='does not match'):m.verify_workspace('wanted')

def test_workspace_required_before_account_read():
    import sonora_selfhost.modal_runtime as m
    with patch.object(m.subprocess,'run') as run:
        with pytest.raises(ConfigError):m.verify_workspace(None)
        run.assert_not_called()

def test_sdk_error_does_not_echo_secret(tmp_path):
    import sonora_selfhost.modal_runtime as m
    with patch.object(m,'_modal_command',side_effect=RuntimeError('SECRETNOTFORECHO')):
        with pytest.raises(ConfigError) as e:m.modal_command(args('status'),tmp_path)
    assert 'SECRETNOTFORECHO' not in str(e.value);assert 'RuntimeError' in str(e.value)

def test_state_rejects_swapped_resource_names(tmp_path):
    n='sonora-user-'+'a'*32
    (tmp_path/'modal-state.json').write_text(json.dumps({'namespace':n,'volume':'unrelated','guard':n+'-state'}))
    with pytest.raises(ConfigError,match='names do not match'):load_state(tmp_path)

def test_guide_needs_no_sdk_or_account(tmp_path,capsys):
    with patch.dict(sys.modules,{'modal':None}):assert modal_command(args('guide'),tmp_path)==0
    assert 'unverified' in capsys.readouterr().out
    assert not list(tmp_path.iterdir())

def test_reset_archives_only_verified_deleted_session(tmp_path):
    import sonora_selfhost.modal_runtime as m
    n='sonora-user-'+'a'*32;s={'namespace':n,'volume':n+'-models','guard':n+'-state','workspace':'test','phases':{},'stopped_verified':True,'models_deleted':True}
    (tmp_path/'modal-state.json').write_text(json.dumps(s));modal=Mock();modal.Volume.objects.list.return_value=[];modal.Dict.objects.list.return_value=[]
    with patch.dict(sys.modules,{'modal':modal}),patch.object(m,'verify_workspace'),patch.object(m,'app_list',return_value=[]):assert modal_command(args('reset'),tmp_path)==0
    assert not (tmp_path/'modal-state.json').exists();assert json.loads((tmp_path/'modal-history'/(n+'.json')).read_text())==s

def test_reset_retains_handles_if_storage_still_exists(tmp_path):
    import sonora_selfhost.modal_runtime as m
    n='sonora-user-'+'a'*32;s={'namespace':n,'volume':n+'-models','guard':n+'-state','workspace':'test','phases':{},'stopped_verified':True,'models_deleted':True}
    (tmp_path/'modal-state.json').write_text(json.dumps(s));modal=Mock();modal.Volume.objects.list.return_value=[types.SimpleNamespace(name=n+'-models')];modal.Dict.objects.list.return_value=[]
    with patch.dict(sys.modules,{'modal':modal}),patch.object(m,'verify_workspace'),patch.object(m,'app_list',return_value=[]):
        with pytest.raises(ConfigError,match='not verified'):modal_command(args('reset'),tmp_path)
    assert (tmp_path/'modal-state.json').exists()

@pytest.mark.parametrize('action',['status','connect'])
def test_completed_old_prepare_does_not_expire_ready_serve(tmp_path,action):
    import sonora_selfhost.modal_runtime as m
    n='sonora-user-'+'a'*32
    s={'namespace':n,'volume':n+'-models','guard':n+'-state','workspace':'test','phases':{'prepare':{'call_id':'cpu','absolute_deadline':1,'queue_deadline':1},'serve':{'call_id':'gpu','absolute_deadline':99999999999,'queue_deadline':99999999999}}}
    (tmp_path/'modal-state.json').write_text(json.dumps(s));(tmp_path/'modal-session-key.json').write_text('{"key":"local-test-only"}')
    modal=Mock();modal.exception.TimeoutError=TimeoutError
    def call(i):
        c=Mock()
        if i=='cpu':c.get.return_value={'status':'prepared'}
        else:c.get.side_effect=TimeoutError()
        return c
    modal.FunctionCall.from_id.side_effect=call
    modal.Dict.from_name.return_value.get.return_value={'url':'https://test.invalid','status':'ready','expires_epoch':99999999999}
    with patch.dict(sys.modules,{'modal':modal}),patch.object(m,'verify_workspace'),patch.object(m,'stop_owned') as stop,patch('sonora_selfhost.provider.AceStep') as provider,patch.object(m,'save_config') as save:
        provider.return_value.models.return_value=['acestep-v15-turbo']
        assert modal_command(args(action),tmp_path)==0
        stop.assert_not_called()
        assert save.called==(action=='connect')
    assert json.loads((tmp_path/'modal-state.json').read_text())['phases']['prepare']['completed_status']=='prepared'

@pytest.mark.parametrize('deleted',[False,True])
def test_later_status_after_explicit_stop_never_deletes_storage(tmp_path,deleted,capsys):
    import sonora_selfhost.modal_runtime as m
    n='sonora-user-'+'a'*32
    s={'namespace':n,'volume':n+'-models','guard':n+'-state','workspace':'test','stopped_verified':True,'models_deleted':deleted,'phases':{'serve':{'call_id':'gpu','absolute_deadline':1}}}
    (tmp_path/'modal-state.json').write_text(json.dumps(s));modal=Mock()
    with patch.dict(sys.modules,{'modal':modal}),patch.object(m,'verify_workspace'),patch.object(m,'stop_owned') as stop:
        assert modal_command(args('status'),tmp_path)==0;stop.assert_not_called()
    modal.FunctionCall.from_id.assert_not_called();modal.Dict.from_name.return_value.get.assert_not_called();modal.Volume.objects.delete.assert_not_called()
    assert ('retained' if not deleted else 'deleted') in capsys.readouterr().out

def test_finished_serve_and_prepare_expiry_do_not_delete_storage(tmp_path):
    import sonora_selfhost.modal_runtime as m
    n='sonora-user-'+'a'*32
    s={'namespace':n,'volume':n+'-models','guard':n+'-state','workspace':'test','phases':{'prepare':{'call_id':'cpu','absolute_deadline':1,'completed_status':'prepared'},'serve':{'call_id':'gpu','absolute_deadline':1}}}
    (tmp_path/'modal-state.json').write_text(json.dumps(s));modal=Mock();modal.exception.TimeoutError=TimeoutError;modal.FunctionCall.from_id.return_value.get.return_value={'status':'session-ended'}
    with patch.dict(sys.modules,{'modal':modal}),patch.object(m,'verify_workspace'),patch.object(m,'stop_owned') as stop:
        assert modal_command(args('status'),tmp_path)==0;stop.assert_not_called()
    assert json.loads((tmp_path/'modal-state.json').read_text())['phases']['serve']['completed_status']=='session-ended'

def test_pending_expired_phase_is_cleaned_not_resubmitted(tmp_path):
    import sonora_selfhost.modal_runtime as m
    n='sonora-user-'+'a'*32
    s={'namespace':n,'volume':n+'-models','guard':n+'-state','workspace':'test','phases':{'serve':{'call_id':'gpu','absolute_deadline':1}}}
    (tmp_path/'modal-state.json').write_text(json.dumps(s));modal=Mock();modal.exception.TimeoutError=TimeoutError;modal.FunctionCall.from_id.return_value.get.side_effect=TimeoutError()
    with patch.dict(sys.modules,{'modal':modal}),patch.object(m,'verify_workspace'),patch.object(m,'stop_owned') as stop:
        with pytest.raises(ConfigError,match='Active phase deadline'):modal_command(args('status'),tmp_path)
        stop.assert_called_once()
    modal.App.assert_not_called()
