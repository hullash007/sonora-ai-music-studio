"""Install, connect, diagnose and launch without any hosted-owner account."""
from __future__ import annotations
import argparse
import getpass
import json
import os
import secrets
import sys
from . import __version__
from .config import Config, ConfigError, data_home, load_config, private_dir, read_key, save_config
from .provider import AceStep, ProviderError
from .store import BusyError, ProcessLock, Store


def parser():
    p = argparse.ArgumentParser(prog='sonora', description='Your music studio. Your server, models and hardware or provider balance.')
    p.add_argument('--version', action='version', version=__version__)
    sub = p.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('setup', help='Connect your ACE-Step endpoint. Does not generate music.')
    setup.add_argument('--endpoint', help='Origin URL of your own ACE-Step REST server.')
    setup.add_argument('--model', help='Choose a model reported by the server.')
    setup.add_argument('--no-key', action='store_true', help='Use an unauthenticated local endpoint (or SONORA_API_KEY).')
    sub.add_parser('doctor', help='Check config and discover models without generating.')
    sub.add_parser('models', help='List models reported by your server, without loading models.')
    start = sub.add_parser('start', help='Start the private loopback browser studio.')
    start.add_argument('--port', type=int, default=8787)
    sub.add_parser('jobs', help='Inspect local jobs, including remote IDs. No submission.')
    rec = sub.add_parser('reconcile', help='Resume tracking a known remote task without resubmitting.')
    rec.add_argument('job_id')
    release = sub.add_parser('acknowledge', help='Release a blocked local job only after checking the provider.')
    release.add_argument('job_id')
    release.add_argument('--remote-outcome-checked', action='store_true', required=True,
                         help='I checked the provider and accept any remote cost. This does not cancel anything.')
    cloud = sub.add_parser('modal', help='Experimental own-account Modal setup/session; not GPU-validated.')
    cloud.add_argument('action', choices=['plan','guide','login','prepare','start','status','connect','stop','reset'])
    cloud.add_argument('--workspace', help='Exact Modal workspace username to verify before allocating resources.')
    cloud.add_argument('--accept-compute', action='store_true', help='Use my own active Modal workspace and accept its compute/build/storage charges.')
    cloud.add_argument('--accept-downloads', action='store_true')
    cloud.add_argument('--delete-models', action='store_true', help='Delete this Sonora session model Volume after stopped/tasks0 verification.')
    engine = sub.add_parser('engine', help='Pinned local ACE-Step engine bootstrap (experimental).')
    engine.add_argument('action', choices=['plan', 'install', 'start', 'verify'])
    engine.add_argument('--accept-downloads', action='store_true', help='Allow source/dependency/model downloads and local disk use.')
    return p


def setup(args, root):
    endpoint = args.endpoint or input('Your ACE-Step server URL [http://127.0.0.1:8001]: ').strip() or 'http://127.0.0.1:8001'
    # Validate the endpoint before asking for a key.
    provisional = Config(endpoint, args.model or 'select-after-discovery')
    env_key = os.environ.get('SONORA_API_KEY')
    key = env_key if env_key is not None else ('' if args.no_key else getpass.getpass('Server API key (hidden; Enter for none): '))
    provider = AceStep(provisional, key)
    try:
        available = provider.models()
    finally:
        provider.close()
    for i, model in enumerate(available, 1):
        print(f'{i}. {model}')
    chosen = args.model
    if chosen is None:
        if len(available) == 1:
            chosen = available[0]
        else:
            try:
                n = int(input('Choose model number: '))
                if n < 1:
                    raise ValueError()
                chosen = available[n - 1]
            except (ValueError, IndexError):
                raise ConfigError('Invalid model choice. Previous configuration was kept.') from None
    if chosen not in available:
        raise ConfigError('Model is not available on that server. Previous configuration was kept.')
    lock = ProcessLock(root)
    try:
        store = Store(root)
        if any(j['status'] not in {'complete','failed','acknowledged'} for j in store.all()):
            try:
                previous = load_config(root)
            except ConfigError:
                previous = None
            if previous and previous.endpoint != provisional.endpoint:
                raise BusyError('Resolve the active job before changing endpoints. Previous configuration was kept.')
        save_config(root, Config(provisional.endpoint, chosen), None if env_key is not None else key)
    finally:
        lock.close()
    print('Connection and model discovery passed. No music generated, GPU readiness not yet verified.')
    print('Saved local configuration. Next: sonora start')
    if env_key is not None:
        print('Using SONORA_API_KEY from your environment; it was not saved. Keep it available when starting.')
    elif key:
        print('Key saved separately with owner-only POSIX permissions. On Windows, protect your profile with NTFS access controls.')


def main(argv=None):
    args = parser().parse_args(argv)
    root = data_home()
    lock = None
    try:
        if args.command == 'modal':
            from .modal_runtime import modal_command
            return modal_command(args, root)
        if args.command == 'engine':
            from .engine import engine_command
            return engine_command(args, root)
        if args.command == 'setup':
            setup(args, root)
            return 0
        if args.command == 'jobs':
            for job in Store(root).all():
                print(json.dumps({k: job[k] for k in ['id','status','model','remote_id','error']}))
            return 0
        if args.command in {'reconcile','acknowledge'}:
            lock = ProcessLock(root)
            store = Store(root)
            job = store.get(args.job_id)
            if not job:
                raise ConfigError('Job not found.')
            if args.command == 'acknowledge':
                if job['status'] not in {'uncertain','retrieval_error','paused'}:
                    raise ConfigError('Only blocked jobs can be acknowledged. Stop Sonora first.')
                store.update(job['id'], expected=(job['status'],), status='acknowledged',
                             error='Locally acknowledged after checking provider. No remote cancellation was performed.')
                print('Local admission released. No remote task was cancelled, deleted or refunded.')
            else:
                config = load_config(root)
                if not job['remote_id'] or job['endpoint'] != config.endpoint:
                    raise ConfigError('A known remote ID and the original endpoint are required. Do not resubmit an uncertain task.')
                if job['status'] not in {'retrieval_error','paused','remote'}:
                    raise ConfigError('This job does not need retrieval reconciliation.')
                store.update(job['id'], expected=(job['status'],), status='remote', error=None)
                print('Ready to resume tracking the same remote task. Run sonora start. No generation submitted.')
            return 0
        config = load_config(root)
        provider = AceStep(config, read_key(root, config))
        if args.command in {'doctor', 'models'}:
            try:
                models = provider.models()
                for model in models:
                    print(('* ' if model == config.model else '  ') + model)
                if config.model not in models:
                    raise ConfigError('Configured model is not currently available. Run sonora setup.')
                if args.command == 'doctor':
                    print(f'Sonora {__version__}: connection and model discovery OK.')
                    print('No generation or model loading performed. GPU, music output and provider balance are not verified.')
                    print('An explicit Generate click in the studio is the real output test and can incur provider costs.')
            finally:
                provider.close()
            return 0
        if not 1 <= args.port <= 65535:
            raise ConfigError('Port must be between 1 and 65535.')
        import uvicorn
        from .server import create_app
        lock = ProcessLock(root)
        store = Store(root)
        token = secrets.token_urlsafe(32)
        app = create_app(store, provider, token=token)
        print(f'Sonora {__version__} | ACE-Step REST | {config.model}')
        print(f'Open privately: http://127.0.0.1:{args.port}/#session={token}')
        print('Keep this URL on this computer. Provider keys never enter the browser.')
        print(f'Library: {root}')
        print('Closing Sonora does not stop an already submitted remote GPU task.')
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)
        return 0
    except (ConfigError, ProviderError, BusyError) as exc:
        print(f'Sonora: {exc}', file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print('Stopped. Existing configuration is unchanged unless setup already completed.', file=sys.stderr)
        return 1
    except OSError:
        print('Sonora: local file or socket operation failed. Check permissions, disk space and port availability.', file=sys.stderr)
        return 1
    finally:
        if lock:
            lock.close()

if __name__ == '__main__':
    raise SystemExit(main())
