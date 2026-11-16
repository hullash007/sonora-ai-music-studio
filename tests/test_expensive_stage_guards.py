"""No model imports: verify guard-boundary semantics and adapter source shape."""
import ast
from pathlib import Path
import unittest
import test_worker as fixtures
from sonora.guard import claim_terminal
from sonora.worker import assert_job_active, GenerationStopped


class ExpensiveStageGuardTests(unittest.TestCase):
    def test_cancellation_before_each_stage_blocks_stage_and_all_later_work(self):
        stages = ['download', 'dit_init', 'llm_init', 'generate']
        for cancel_at in range(len(stages)):
            c = fixtures.WorkerFixture(); c.setUp()
            executed = []
            try:
                for index, stage in enumerate(stages):
                    if index == cancel_at:
                        claim_terminal(c.guard, c.run_config, c.job_id, 'cancelled')
                    try:
                        assert_job_active(c.guard, c.run_config, c.job_id)
                    except GenerationStopped:
                        break
                    executed.append(stage)
                self.assertEqual(executed, stages[:cancel_at])
            finally:
                c.tearDown()

    def test_guard_pass_then_cancellation_stops_next_observable_boundary(self):
        c = fixtures.WorkerFixture(); c.setUp()
        try:
            assert_job_active(c.guard, c.run_config, c.job_id)
            claim_terminal(c.guard, c.run_config, c.job_id, 'cancelled')
            # An already entered synchronous phase cannot be interrupted by a
            # Python precheck. The NEXT boundary must observe cancellation.
            with self.assertRaises(GenerationStopped):
                assert_job_active(c.guard, c.run_config, c.job_id)
        finally:
            c.tearDown()

    def test_real_adapter_has_guard_before_each_expensive_call(self):
        source = Path(__file__).resolve().parents[1] / 'infra/modal/sonora_staging.py'
        tree = ast.parse(source.read_text())
        generation = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'generate_audio')
        calls = sorted([n for n in ast.walk(generation) if isinstance(n, ast.Call)], key=lambda n:n.lineno)
        guard_line = 0; previous_expensive = 0; checked = []
        for call in calls:
            name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ''
            if name == 'assert_job_active': guard_line = call.lineno
            if name in {'ensure_model_downloaded', 'initialize_service', 'initialize', 'generate_music'}:
                self.assertGreater(guard_line, previous_expensive, name)
                self.assertLess(guard_line, call.lineno, name)
                previous_expensive = call.lineno; checked.append(name)
        self.assertEqual(checked, ['ensure_model_downloaded','initialize_service','initialize','generate_music'])
