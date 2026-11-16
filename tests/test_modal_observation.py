"""Execute only extracted pure observation adapters, never import the deployment."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class PollTimeout(Exception):
    pass


class FunctionTimeout(PollTimeout):
    pass


class InputCancellation(BaseException):
    pass


class ModalObservationTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'infra/modal/sonora_staging.py'
        tree = ast.parse(source.read_text())
        portal = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'music_portal')
        names = {'call_result_state', 'generation_termination_confirmed'}
        nodes = [n for n in portal.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes), 2)
        self.error = None
        self.stats = SimpleNamespace(backlog=0, num_running_inputs=0, num_total_runners=0)
        self.polls = []

        def get(timeout):
            self.polls.append(timeout)
            if self.error is not None:
                raise self.error
            return {'status': 'complete'}

        fake_modal = SimpleNamespace(
            FunctionCall=SimpleNamespace(from_id=lambda call_id: SimpleNamespace(get=get)),
            exception=SimpleNamespace(FunctionTimeoutError=FunctionTimeout, TimeoutError=PollTimeout, InputCancellation=InputCancellation),
        )
        self.env = {'Any': object, 'modal': fake_modal, 'generate_track': SimpleNamespace(get_current_stats=lambda: self.stats)}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<observation-adapter>', 'exec'), self.env)

    def evidence(self):
        return self.env['generation_termination_confirmed']('call-test')

    def stopped(self):
        return self.evidence()['termination_confirmed']

    def test_returned_call_and_zero_workers_confirms_stop(self):
        self.assertTrue(self.stopped())
        self.assertEqual(self.polls, [0])
        self.assertEqual(self.evidence()['state'], 'returned')
        self.assertEqual(self.evidence()['call_id'], 'call-test')

    def test_terminal_input_with_live_container_does_not_confirm_stop(self):
        self.stats.num_total_runners = 1
        self.assertFalse(self.stopped())

    def test_backlog_or_running_input_blocks_cleanup(self):
        for field in ('backlog', 'num_running_inputs'):
            with self.subTest(field=field):
                setattr(self.stats, field, 1)
                self.assertFalse(self.stopped())
                setattr(self.stats, field, 0)

    def test_function_timeout_is_observed_failure(self):
        self.error = FunctionTimeout()
        self.assertEqual(self.env['call_result_state']('call-test'), 'execution_timed_out')
        self.assertTrue(self.stopped())

    def test_poll_timeout_is_pending_not_failure(self):
        self.error = PollTimeout()
        self.assertIn(self.env['call_result_state']('call-test'), {'pending', 'unknown'})
        self.assertFalse(self.stopped())

    def test_cancellation_still_requires_zero_containers(self):
        self.error = InputCancellation()
        self.stats.num_total_runners = 1
        self.assertFalse(self.stopped())
        self.stats.num_total_runners = 0
        self.assertTrue(self.stopped())

    def test_unknown_runtime_rpc_or_deserialization_error_is_not_stop(self):
        self.error = RuntimeError('unverified error')
        self.assertFalse(self.stopped())
        self.assertIn(self.env['call_result_state']('call-test'), {'pending', 'unknown'})

    def test_stats_error_does_not_confirm_stop(self):
        def fail():
            raise RuntimeError('stats unavailable')
        self.env['generate_track'].get_current_stats = fail
        self.assertFalse(self.stopped())
