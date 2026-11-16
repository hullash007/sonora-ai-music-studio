"""Transactional local jobs. A submission is claimed durably before any network call."""
from __future__ import annotations
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from .config import private_dir

ACTIVE = ('queued', 'submitting', 'remote', 'uncertain', 'retrieval_error', 'paused')
TERMINAL = ('complete', 'failed', 'acknowledged')

class BusyError(ValueError):
    pass

class Store:
    def __init__(self, root: Path):
        self.root = private_dir(root)
        self.path = self.root / 'library.sqlite3'
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                prompt TEXT NOT NULL, lyrics TEXT NOT NULL, duration INTEGER NOT NULL,
                model TEXT NOT NULL, endpoint TEXT NOT NULL, remote_id TEXT,
                error TEXT, actual_duration REAL)''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def all(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 500')]

    def create(self, request_key, prompt, lyrics, duration, model, endpoint):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id FROM jobs WHERE request_key=?', (request_key,)).fetchone()
            if old:
                previous = dict(db.execute('SELECT * FROM jobs WHERE id=?', (old['id'],)).fetchone())
                if (prompt, lyrics, duration, model, endpoint) != tuple(previous[k] for k in ('prompt','lyrics','duration','model','endpoint')):
                    raise ValueError('Request key already used for different inputs.')
                return old['id']
            if db.execute('SELECT 1 FROM jobs WHERE status NOT IN (?,?,?) LIMIT 1', TERMINAL).fetchone():
                raise BusyError('One job is already active or needs reconciliation. Check its status before submitting again.')
            job_id = uuid.uuid4().hex
            now = time.time()
            db.execute('INSERT INTO jobs (id,request_key,status,created,updated,prompt,lyrics,duration,model,endpoint) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (job_id, request_key, 'queued', now, now, prompt, lyrics, duration, model, endpoint))
            return job_id

    def update(self, job_id, *, expected: tuple[str, ...], **fields):
        allowed = {'status', 'remote_id', 'error', 'actual_duration'}
        if not fields or set(fields) - allowed:
            raise ValueError('Invalid job update.')
        fields['updated'] = time.time()
        with self.connect() as db:
            query = ','.join(f'{key}=?' for key in fields)
            marks = ','.join('?' for _ in expected)
            cur = db.execute(f'UPDATE jobs SET {query} WHERE id=? AND status IN ({marks})',
                             (*fields.values(), job_id, *expected))
            return cur.rowcount == 1

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='uncertain', error=? WHERE status='submitting'",
                       ('Sonora stopped during submission. Check the provider; this request will not be resubmitted.',))

    def audio_path(self, job_id):
        if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('Invalid job ID.')
        return self.root / f'{job_id}.wav'


class ProcessLock:
    """Held for the process lifetime. OS lock releases automatically after a crash."""
    def __init__(self, root: Path):
        self.file = (private_dir(root) / 'studio.lock').open('a+b')
        try:
            if __import__('os').name == 'nt':
                import msvcrt
                self.file.write(b'0'); self.file.flush(); self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise BusyError('Sonora is already using this data directory. Stop that process first.') from None

    def close(self):
        self.file.close()


class Worker:
    def __init__(self, store, provider, *, poll_seconds=3, max_poll_seconds=3600):
        self.store, self.provider = store, provider
        self.poll_seconds, self.max_poll_seconds = poll_seconds, max_poll_seconds
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        self.store.recover()
        self.thread = threading.Thread(target=self.run, name='sonora-jobs', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=35)
        # A remote task is NOT cancelled when the local process stops.

    def run(self):
        while not self.stop.is_set():
            for job in reversed(self.store.all()):
                if self.stop.is_set():
                    break
                if job['status'] in {'queued', 'remote'}:
                    try:
                        self.process(job['id'])
                    except Exception:
                        # Unknown local failures never trigger another submit.
                        self.store.update(job['id'], expected=('submitting',), status='uncertain',
                                          error='Local failure during submission. Check the provider before continuing.')
                        self.store.update(job['id'], expected=('remote',), status='retrieval_error',
                                          error='Local failure while tracking a remote task. Resume retrieval, not generation.')
            self.stop.wait(self.poll_seconds)

    def process(self, job_id):
        from .provider import ProviderError, SubmissionUnknown
        job = self.store.get(job_id)
        if not job or job['status'] not in {'queued', 'remote'}:
            return
        if job['endpoint'] != self.provider.config.endpoint:
            self.store.update(job_id, expected=(job['status'],), status='paused', error='This job belongs to another endpoint. Restore that connection before resuming.')
            return
        if job['status'] == 'queued':
            # Free discovery first. A failed preflight is definitive: nothing submitted.
            try:
                if job['model'] not in self.provider.models():
                    raise ProviderError('Selected model is not loaded. No generation was submitted.')
            except ProviderError as exc:
                self.store.update(job_id, expected=('queued',), status='failed', error=str(exc))
                return
            if not self.store.update(job_id, expected=('queued',), status='submitting'):
                return
            try:
                remote_id = self.provider.submit(job['prompt'], job['lyrics'], job['duration'], job['model'])
            except SubmissionUnknown as exc:
                self.store.update(job_id, expected=('submitting',), status='uncertain', error=str(exc))
                return
            self.store.update(job_id, expected=('submitting',), status='remote', remote_id=remote_id)
            job = self.store.get(job_id)
        began = time.monotonic()
        while not self.stop.is_set() and time.monotonic() - began < self.max_poll_seconds:
            try:
                status, audio = self.provider.poll(job['remote_id'])
                if status == 'failed':
                    self.store.update(job_id, expected=('remote',), status='failed', error='Provider reports generation failed. Check its billing for any charge.')
                    return
                if status == 'complete':
                    try:
                        actual = self.provider.download(audio, self.store.audio_path(job_id), job['duration'])
                    except ProviderError as exc:
                        self.store.update(job_id, expected=('remote',), status='retrieval_error', error=str(exc))
                        return
                    self.store.update(job_id, expected=('remote',), status='complete', actual_duration=actual, error=None)
                    return
                self.store.update(job_id, expected=('remote',), error=None)
            except ProviderError as exc:
                # Poll failures can recover without replaying a potentially billable submission.
                self.store.update(job_id, expected=('remote',), error=str(exc))
            if self.stop.wait(self.poll_seconds):
                return
        if not self.stop.is_set():
            self.store.update(job_id, expected=('remote',), status='retrieval_error',
                              error='Tracking paused after one hour. Remote task may still run. Use sonora reconcile to resume without resubmitting.')
