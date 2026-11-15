"""One-shot Actions deployment. No model call, sentinel creation, or retries."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time

APP_NAME = 'sonora-staging'
# Prior immutable guard entries remain in Modal; never delete or re-label them.
ATTEMPT_KEY = 'sonora:deployment-validation:2026-11-16:ui07-rc1-b514fa3688c7'
MAX_BUILD_SECONDS = 900


def main() -> int:
    if os.environ.get('GITHUB_RUN_ATTEMPT') != '1':
        raise RuntimeError('Only the first workflow attempt is allowed')
    if os.environ.get('GITHUB_REF') != 'refs/heads/feat/phase-1-library':
        raise RuntimeError('Wrong branch')
    if not os.environ.get('MODAL_TOKEN_ID') or not os.environ.get('MODAL_TOKEN_SECRET'):
        raise RuntimeError('Modal credentials are missing')
    import modal
    guard = modal.Dict.from_name('sonora-staging-guard', create_if_missing=False)
    claimed = guard.put(ATTEMPT_KEY, {
        'run_id': os.environ['GITHUB_RUN_ID'],
        'sha': os.environ['GITHUB_SHA'],
        'claimed_at': time.time(),
    }, skip_if_exists=True)
    if not claimed:
        raise RuntimeError('Deployment attempt already consumed; no retry')
    success = False
    try:
        subprocess.run([
            sys.executable, '-m', 'modal', 'deploy',
            'infra/modal/sonora_staging.py', '--name', APP_NAME,
        ], check=True, timeout=MAX_BUILD_SECONDS)
        success = True
        print('Staging deployment completed. No GPU invocation requested.')
        return 0
    finally:
        if not success:
            result = subprocess.run([
                sys.executable, '-m', 'modal', 'app', 'list', '--json',
            ], capture_output=True, text=True, timeout=45, check=True)
            for app in json.loads(result.stdout):
                if app.get('description') == APP_NAME and app.get('state') != 'stopped':
                    subprocess.run([
                        sys.executable, '-m', 'modal', 'app', 'stop',
                        app['app_id'], '--yes',
                    ], check=True, timeout=60)
            print('Deploy failed/timed out. Stop requested for staging only. No retry.')


if __name__ == '__main__':
    raise SystemExit(main())
