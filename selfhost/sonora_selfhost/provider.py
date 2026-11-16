"""Official ACE-Step REST protocol. No submit retries and no redirect following."""
from __future__ import annotations
import json
import os
import time
import wave
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
import httpx
from .config import Config, validate_key

MAX_JSON = 2 * 1024 * 1024
MAX_AUDIO = 128 * 1024 * 1024

class ProviderError(RuntimeError):
    """Sanitized diagnostic: deliberately contains no response body or key."""

class SubmissionUnknown(ProviderError):
    pass

class DefinitiveRejection(ProviderError):
    pass


def validate_wav(path: Path, requested: int) -> float:
    try:
        with wave.open(str(path), 'rb') as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if not (8000 <= rate <= 192000 and wav.getnchannels() in {1, 2} and wav.getsampwidth() in {2, 3, 4}):
                raise ValueError()
            actual = frames / rate
            if not requested - 2 <= actual <= requested + 2:
                raise ValueError()
            remaining = frames
            while remaining:
                count = min(remaining, 65536)
                chunk = wav.readframes(count)
                if len(chunk) != count * wav.getnchannels() * wav.getsampwidth():
                    raise ValueError()
                remaining -= count
            return actual
    except (OSError, wave.Error, EOFError, ValueError, ZeroDivisionError):
        raise ProviderError('Output is not a complete PCM WAV of the requested duration. Remote generation may have succeeded.') from None


class AceStep:
    def __init__(self, config: Config, key: str = '', transport=None):
        self.config = config
        key = validate_key(key)
        self.client = httpx.Client(base_url=config.endpoint, headers={'Authorization': f'Bearer {key}'} if key else {},
                                   timeout=httpx.Timeout(30, connect=10), follow_redirects=False,
                                   trust_env=False, transport=transport)

    def close(self):
        self.client.close()

    def _json(self, method: str, path: str, **kwargs):
        try:
            with self.client.stream(method, path, **kwargs) as response:
                # Even 4xx/5xx after submitting can be a proxy timeout after acceptance.
                if not 200 <= response.status_code < 300:
                    raise ProviderError(f'Provider HTTP {response.status_code}. Check the server and authentication.')
                raw = bytearray()
                began = time.monotonic()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_JSON or time.monotonic() - began > 60:
                        raise ProviderError('Provider JSON response is too large.')
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get('code') != 200 or result.get('error'):
                    raise ProviderError('Provider reported an error or incompatible response.')
                return result['data']
        except ProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ProviderError('Provider request failed or returned an incompatible response.') from None

    def models(self) -> list[str]:
        data = self._json('GET', '/v1/models')
        try:
            # Current upstream includes is_loaded; older compatible servers may omit it.
            models = [m['name'] for m in data['models'] if m.get('is_loaded', True)
                      and 'text2music' in m.get('supported_task_types', ['text2music'])]
            if not models or any(not isinstance(m, str) or not m or len(m) > 200 for m in models):
                raise ValueError()
            return list(dict.fromkeys(models))
        except (TypeError, KeyError, ValueError, AttributeError):
            raise ProviderError('Server did not report any available loaded music models.') from None

    def submit(self, prompt: str, lyrics: str, duration: int, model: str) -> str:
        try:
            data = self._json('POST', '/release_task', json={
                'prompt': prompt, 'lyrics': lyrics, 'audio_duration': duration, 'model': model,
                'batch_size': 1, 'audio_format': 'wav', 'thinking': False,
                'use_cot_caption': False, 'use_cot_language': False, 'sample_mode': False,
                'use_format': False, 'task_type': 'text2music',
            })
            task = data['task_id']
            if not isinstance(task, str) or not task or len(task) > 200:
                raise ValueError()
            return task
        except (ProviderError, KeyError, TypeError, ValueError):
            raise SubmissionUnknown('Submission outcome unknown. Check your provider before creating another job. Sonora will not resubmit.') from None

    def poll(self, remote_id: str) -> tuple[str, str | None]:
        rows = self._json('POST', '/query_result', json={'task_id_list': [remote_id]})
        try:
            row = next(r for r in rows if r['task_id'] == remote_id)
            status = row['status']
            if type(status) is not int:
                raise ValueError()
            if status == 0:
                return 'running', None
            if status == 2:
                return 'failed', None
            if status != 1:
                raise ValueError()
            results = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
            if not isinstance(results, list) or len(results) != 1 or results[0].get('status') != 1:
                raise ValueError()
            audio = results[0]['file']
            self.audio_url(audio)
            return 'complete', audio
        except (KeyError, TypeError, ValueError, StopIteration, AttributeError):
            raise ProviderError('Provider task response is incompatible. No task was resubmitted.') from None

    def audio_url(self, value: str) -> str:
        if not isinstance(value, str) or len(value) > 8192:
            raise ProviderError('Invalid audio URL.')
        url = urljoin(self.config.endpoint + '/', value)
        source, target = urlsplit(self.config.endpoint), urlsplit(url)
        if (target.scheme, target.hostname, target.port) != (source.scheme, source.hostname, source.port):
            raise ProviderError('Refused audio from another origin. No credential was forwarded.')
        query = parse_qs(target.query, keep_blank_values=True)
        if (target.username or target.password or target.fragment or target.path != '/v1/audio'
                or set(query) != {'path'} or len(query['path']) != 1 or not query['path'][0].lower().endswith('.wav')):
            raise ProviderError('Refused an unexpected audio route or format.')
        return url

    def download(self, value: str, destination: Path, duration: int) -> float:
        url = self.audio_url(value)
        tmp = destination.with_suffix('.partial')
        try:
            with self.client.stream('GET', url) as response:
                if response.status_code != 200:
                    raise ProviderError('Audio download failed. No new generation was submitted.')
                content_type = response.headers.get('content-type', '').split(';')[0].lower()
                if content_type not in {'audio/wav', 'audio/x-wav', 'audio/wave', 'application/octet-stream'}:
                    raise ProviderError('Server returned a non-WAV content type.')
                size = 0
                began = time.monotonic()
                with tmp.open('wb') as f:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_AUDIO or time.monotonic() - began > 120:
                            raise ProviderError('Audio exceeded the local download limit.')
                        f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
            actual = validate_wav(tmp, duration)
            os.replace(tmp, destination)
            return actual
        except (httpx.HTTPError, OSError):
            raise ProviderError('Audio download/storage failed. Remote music may be ready; do not generate again to retry retrieval.') from None
        finally:
            tmp.unlink(missing_ok=True)
