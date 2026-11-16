"""Durable new-visitor success quota. Does not replace shared credit guards.

Account enrollment and job reservations use atomic insert-only records. A failed
job frees capacity only with an authoritative failed/cancelled guard outcome,
a positively terminal remote call, and zero GPU activity. Unknown work stays
reserved. No browser value or submitted email selects the account.
"""
from __future__ import annotations

import hashlib
import time
from fastapi import HTTPException
from sonora.guard import read_run_config_identity, read_terminal_state

PREFIX = 'visitor-quota-v1:'
POLICY_KEY = PREFIX + 'policy'
LIMIT = 3


def account_key(subject: str) -> str:
    if not isinstance(subject, str) or not subject:
        raise HTTPException(503, 'Account allowance is unavailable. Please try again later.')
    return PREFIX + 'account:' + hashlib.sha256(subject.encode()).hexdigest()


class VisitorQuota:
    def __init__(self, store, stats, call_state, clock=time.time):
        self.store = store
        self.stats = stats
        self.call_state = call_state
        self.clock = clock

    def register(self, identity):
        policy = self.store.get(POLICY_KEY)
        if not isinstance(policy, dict) or policy.get('limit') != LIMIT or policy.get('ready') is not True:
            raise HTTPException(503, 'Account allowance is being prepared. Please try again later.')
        if not isinstance(identity, dict):
            raise HTTPException(401, 'Sign in with Google')
        key = account_key(identity.get('sub'))
        # The owner role is assigned by verified server-side binding, never input.
        proposed = {'kind': 'visitor-quota-account-v1', 'exempt': identity.get('role') == 'owner', 'registered_at': self.clock()}
        self.store.put(key, proposed, skip_if_exists=True)
        row = self.store.get(key)
        if not isinstance(row, dict) or row.get('kind') != proposed['kind'] or type(row.get('exempt')) is not bool:
            raise HTTPException(503, 'Account allowance needs review. Please try again later.')
        return key, row

    def status(self, identity):
        key, row = self.register(identity)
        if row['exempt']:
            return {'limited': False}
        completed = pending = 0
        from sonora.shared import ScopedGuard
        for record_key in self.store.keys():
            if not record_key.startswith(key + ':job:'):
                continue
            record = self.store.get(record_key)
            if not isinstance(record, dict) or set(record) != {'job_id', 'run_id'}:
                raise HTTPException(503, 'Account allowance needs review. Please try again later.')
            guard = ScopedGuard(self.store)
            guard.select(record['run_id'])
            config = read_run_config_identity(guard)
            terminal = read_terminal_state(guard, config, record['job_id'])
            if terminal and terminal['state'] == 'complete':
                completed += 1
                continue
            call = self.store.get('shared-call:' + record['job_id'])
            safely_failed = (terminal and terminal['state'] in {'failed', 'cancelled'} and call
                             and self.call_state(call) in {'returned', 'execution_timed_out', 'failed', 'input_cancelled'}
                             and self.stats())
            if not safely_failed:
                pending += 1
        return {'limited': True, 'limit': LIMIT, 'completed': completed, 'pending': pending,
                'remaining': max(0, LIMIT - completed - pending)}

    def check(self, identity):
        status = self.status(identity)
        if status.get('limited') and status['remaining'] == 0:
            detail = ('You have created your three free tracks.' if not status['pending']
                      else 'Your account allowance is reserved by existing work. Wait for its result; uncertain jobs need review.')
            raise HTTPException(429, detail)
        return status

    def reserve(self, identity, job_id, config):
        status = self.check(identity)
        if not status.get('limited'):
            return
        key = account_key(identity['sub']) + ':job:' + job_id
        value = {'job_id': job_id, 'run_id': config.run_id}
        self.store.put(key, value, skip_if_exists=True)
        if self.store.get(key) != value:
            raise HTTPException(503, 'Account allowance reservation needs review.')
