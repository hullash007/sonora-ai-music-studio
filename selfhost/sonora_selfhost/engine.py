"""Experimental Linux NVIDIA bootstrap, separate from the lightweight client.

No cloud account, credential creation, resource provisioning or hidden GPU test.
Upstream source/dependency lock and checkpoint content are pinned and verified.
"""
from __future__ import annotations
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from .config import ConfigError, atomic_json, private_dir
from .store import ProcessLock

LOCK = Path(__file__).with_name('upstream-lock.json')


def manifest():
    return json.loads(LOCK.read_text())


def run(command, cwd=None, env=None):
    # Subprocess output can include local paths but never a supplied key argument.
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise ConfigError('Engine command failed. Setup is incomplete; inspect the upstream output above. No generation test was run.')


def check_source(root, spec):
    if not (root / '.git').is_dir():
        raise ConfigError('Pinned engine source is not installed. Run sonora engine install --accept-downloads.')
    result = subprocess.run(['git','rev-parse','HEAD'], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode or result.stdout.strip() != spec['source_revision']:
        raise ConfigError('Engine source revision does not match the bundled lock. Refusing to start.')
    result = subprocess.run(['git','status','--porcelain','--untracked-files=no'], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode or result.stdout.strip():
        raise ConfigError('Pinned engine source was modified. Restore it before using this bootstrap.')


def verify_models(root, spec):
    base = root / 'checkpoints'
    for entry in spec['models']:
        path = base / entry['path']
        if not path.is_file() or path.is_symlink() or path.stat().st_size != entry['size']:
            raise ConfigError('Pinned model files are missing or differ in size. Re-run the engine install to finish downloads.')
        if 'sha256' in entry:
            digest = hashlib.sha256()
        else:
            digest = hashlib.sha1(b'blob ' + str(entry['size']).encode() + b'\0')
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                digest.update(chunk)
        expected = entry.get('sha256', entry.get('git_blob_sha1'))
        if digest.hexdigest() != expected:
            raise ConfigError('Pinned model checksum mismatch. Refusing to load it. Remove the damaged file and re-run install.')


def check_tools(spec):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ConfigError('This experimental engine bootstrap targets Linux x86_64 NVIDIA only. The client can connect to an independently installed upstream server on other platforms.')
    for name in ['git','uv','nvidia-smi','ffmpeg']:
        if not shutil.which(name):
            raise ConfigError(f'Engine prerequisite missing: {name}. Install it yourself, then retry. Sonora does not modify system packages.')
    version = subprocess.run(['uv','--version'], capture_output=True, text=True, check=False)
    if version.returncode or version.stdout.split()[1] != spec['uv_version']:
        raise ConfigError(f'Use uv {spec["uv_version"]} for this locked bootstrap.')
    gpu = subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader'], capture_output=True, text=True, check=False)
    if gpu.returncode or not gpu.stdout.strip():
        raise ConfigError('NVIDIA driver/GPU is unavailable. This is not a successful engine installation.')
    print('Detected GPU:', gpu.stdout.strip())


def engine_command(args, home):
    spec = manifest()
    engine = home / 'engine' / 'ACE-Step-1.5'
    if args.action == 'plan':
        print('EXPERIMENTAL own-GPU route, not yet validated on real hardware by Sonora.')
        print('No cloud account or free credits are created. Cloud machines can bill while running.')
        print('Target: Linux x86_64 NVIDIA; Python 3.12; git, ffmpeg, nvidia-smi; uv ' + spec['uv_version'])
        print('Upstream source:', spec['source_repository'], spec['source_revision'])
        print('Models:', spec['model_repository'], spec['model_revision'])
        print(f'Model download: {spec["model_bytes"]:,} bytes (~10.1 GB decimal); allow at least 30 GB free for dependencies and build caches.')
        print('Upstream recommends a CUDA GPU. Its hardware claims are not Sonora validation; read the upstream model/code terms.')
        print('Install: sonora engine install --accept-downloads')
        print('Start: sonora engine start (loads models, uses your hardware; no music task submitted)')
        print('Connect: sonora setup --endpoint http://127.0.0.1:8001 --no-key')
        print('Then sonora start and explicitly generate a short test. Only real output validates GPU inference.')
        return 0
    private_dir(home)
    check_tools(spec)
    lock = ProcessLock(home / 'engine')
    try:
        if args.action == 'install':
            if not args.accept_downloads:
                raise ConfigError('Read sonora engine plan, then add --accept-downloads to allow source, dependencies and ~10.1 GB of model downloads.')
            if shutil.disk_usage(home).free < 30 * 1024**3:
                raise ConfigError('At least 30 GiB free is required before this engine install. Sonora will not delete your files.')
            if not engine.exists():
                run(['git','clone','--no-checkout',spec['source_repository'],str(engine)])
                run(['git','checkout','--detach',spec['source_revision']], cwd=engine)
            check_source(engine, spec)
            run(['uv','sync','--frozen','--python','3.12'], cwd=engine)
            # Run only the installed upstream HF client, with pinned repository revision.
            script = ('from huggingface_hub import snapshot_download; '
                      'snapshot_download(repo_id=' + repr(spec['model_repository']) + ',revision=' + repr(spec['model_revision']) +
                      ',local_dir="checkpoints")')
            run(['uv','run','--frozen','--no-sync','python','-c',script], cwd=engine)
            verify_models(engine, spec)
            atomic_json(home / 'engine' / 'installed.json', {'source_revision':spec['source_revision'],
                        'model_revision':spec['model_revision'], 'gpu_inference_verified':False})
            print('Pinned source, dependencies and model checksums installed. GPU inference is still UNVERIFIED.')
            print('Next: sonora engine start. First actual Generate may use billable hardware.')
        elif args.action == 'verify':
            check_source(engine, spec)
            verify_models(engine, spec)
            print('Source revision and model checksums match. This is NOT an inference/GPU test.')
        elif args.action == 'start':
            check_source(engine, spec)
            verify_models(engine, spec)
            env = os.environ.copy()
            # Never inherit hosted account settings, resource IDs or another app's URL.
            for name in list(env):
                if name.startswith('ACESTEP_'):
                    del env[name]
            env.update({'ACESTEP_CONFIG_PATH':'acestep-v15-turbo','ACESTEP_INIT_LLM':'false',
                        'ACESTEP_NO_INIT':'false','ACESTEP_DEVICE':'cuda','ACESTEP_DOWNLOAD_SOURCE':'huggingface',
                        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
                        'ACESTEP_API_HOST':'127.0.0.1','ACESTEP_API_PORT':'8001'})
            print('Starting the pinned local engine, bound only to 127.0.0.1:8001. No music task is submitted.')
            print('This holds your GPU until stopped. Cloud machine billing is your responsibility.')
            run(['uv','run','--frozen','--no-sync','acestep-api','--host','127.0.0.1','--port','8001'], cwd=engine, env=env)
        return 0
    finally:
        lock.close()
