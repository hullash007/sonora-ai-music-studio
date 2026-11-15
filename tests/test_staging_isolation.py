from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
STAGING_FILE = ROOT / "infra" / "modal" / "sonora_staging.py"

# These calls may exist in the staging source, but only in executable
# function bodies. They must not run while Python imports the module.
FORBIDDEN_CALL_NAMES = {
    "spawn",
    "cancel",
    "remote",
    "deploy",
    "generate_track",
    "generate_music",
    "ensure_model_downloaded",
}

SHELL_EXECUTION_CALLS = {
    "system",
    "popen",
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",
}


def call_target_name(node: ast.Call) -> str | None:
    """Return the final name in a call target, e.g. `spawn` in `fn.spawn()`."""
    target = node.func

    if isinstance(target, ast.Name):
        return target.id

    if isinstance(target, ast.Attribute):
        return target.attr

    return None


def call_target_path(node: ast.Call) -> str:
    """Return a dotted call target where possible."""
    parts: list[str] = []
    target = node.func

    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value

    if isinstance(target, ast.Name):
        parts.append(target.id)

    return ".".join(reversed(parts))


def literal_strings(node: ast.AST) -> list[str]:
    """Collect literal strings from a call expression for shell-command checks."""
    values: list[str] = []

    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.append(child.value)

    return values


class ImportTimeCallVisitor(ast.NodeVisitor):
    """Find sensitive calls that are not lexically inside a function body.

    Function decorators, defaults, annotations and type parameters are
    deliberately visited outside the function-body context. Class bodies are
    also treated as import-time execution contexts.
    """

    def __init__(self) -> None:
        self.function_depth = 0
        self.violations: list[tuple[int, str, str]] = []
        self.safe_calls: list[tuple[int, str, str]] = []

    def _visit_outside_function_body(self, nodes: Iterable[ast.AST | None]) -> None:
        previous_depth = self.function_depth
        self.function_depth = 0

        try:
            for node in nodes:
                if node is not None:
                    self.visit(node)
        finally:
            self.function_depth = previous_depth

    def _visit_function_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Decorators, defaults, annotations and type parameters are not
        # function-body execution and must not contain sensitive calls.
        outside_nodes: list[ast.AST | None] = [
            *node.decorator_list,
            node.args,
            node.returns,
            *getattr(node, "type_params", []),
        ]
        self._visit_outside_function_body(outside_nodes)

        previous_depth = self.function_depth
        self.function_depth = previous_depth + 1

        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.function_depth = previous_depth

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Lambda defaults are outside the lambda body.
        self._visit_outside_function_body([node.args])

        previous_depth = self.function_depth
        self.function_depth = previous_depth + 1

        try:
            self.visit(node.body)
        finally:
            self.function_depth = previous_depth

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Class decorators are evaluated outside a function body.
        self._visit_outside_function_body(node.decorator_list)

        # Base classes and class keywords are expressions in the surrounding
        # scope, so preserve the current function depth for those expressions.
        for base in node.bases:
            self.visit(base)

        for keyword in node.keywords:
            self.visit(keyword)

        # A class body is not a function body.
        previous_depth = self.function_depth
        self.function_depth = 0

        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self.function_depth = previous_depth

    def visit_Call(self, node: ast.Call) -> None:
        name = call_target_name(node)
        path = call_target_path(node)

        is_sensitive_call = name in FORBIDDEN_CALL_NAMES

        # Also detect shell-based `modal deploy` commands.
        is_shell_modal_deploy = False
        if name in SHELL_EXECUTION_CALLS:
            command_text = " ".join(literal_strings(node)).lower()
            is_shell_modal_deploy = bool(
                re.search(r"\bmodal(?:\.exe)?\s+deploy\b", command_text)
                or re.search(r"\b-m\s+modal\s+deploy\b", command_text)
            )

        if is_sensitive_call or is_shell_modal_deploy:
            description = path or name or "<unknown call>"
            entry = (getattr(node, "lineno", 0), description, "sensitive call")

            if self.function_depth > 0:
                self.safe_calls.append(entry)
            else:
                self.violations.append(entry)

        self.generic_visit(node)


def inspect_source(source: str) -> ImportTimeCallVisitor:
    tree = ast.parse(source)
    visitor = ImportTimeCallVisitor()
    visitor.visit(tree)
    return visitor


class StagingIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not STAGING_FILE.is_file():
            raise FileNotFoundError(f"Staging source not found: {STAGING_FILE}")

        cls.source = STAGING_FILE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(STAGING_FILE))
        cls.visitor = ImportTimeCallVisitor()
        cls.visitor.visit(cls.tree)

    def test_uses_separate_modal_app(self) -> None:
        self.assertRegex(
            self.source,
            r'APP_NAME\s*=\s*["\']sonora-staging["\']',
        )

    def test_uses_staging_only_resources(self) -> None:
        expected = {
            "sonora-staging-checkpoints",
            "sonora-staging-audio",
            "sonora-staging-jobs",
            "sonora-staging-portal",
        }

        self.assertTrue(
            expected.issubset(
                set(
                    re.findall(
                        r'["\'](sonora-staging-[a-z-]+)["\']',
                        self.source,
                    )
                )
            )
        )

        forbidden_resources = {
            "ace-step-instinct-portal",
            "ace-step-15-api",
            "ace-step-checkpoints",
            "ace-step-audio",
            "ace-step-jobs",
            "ace-step-portal",
        }

        for resource in forbidden_resources:
            with self.subTest(resource=resource):
                self.assertNotIn(resource, self.source)

    def test_sensitive_calls_are_not_at_import_time(self) -> None:
        formatted = [
            f"line {line}: {description} ({kind})"
            for line, description, kind in self.visitor.violations
        ]

        self.assertEqual(
            self.visitor.violations,
            [],
            "Sensitive execution calls found outside function bodies:\n"
            + "\n".join(formatted),
        )

    def test_expected_execution_calls_remain_inside_function_bodies(self) -> None:
        safe_names = {
            description.rsplit(".", 1)[-1]
            for _, description, _ in self.visitor.safe_calls
        }

        self.assertIn("spawn", safe_names)
        self.assertIn("generate_music", safe_names)
        self.assertIn("ensure_model_downloaded", safe_names)

    def test_detects_top_level_spawn_call(self) -> None:
        visitor = inspect_source("worker.spawn()")

        self.assertEqual(len(visitor.violations), 1)
        self.assertEqual(visitor.violations[0][1], "worker.spawn")

    def test_detects_spawn_in_function_default(self) -> None:
        visitor = inspect_source(
            "def submit(job=worker.spawn()):\n"
            "    return job\n"
        )

        self.assertEqual(len(visitor.violations), 1)

    def test_detects_spawn_in_function_decorator(self) -> None:
        visitor = inspect_source(
            "@worker.spawn()\n"
            "def submit():\n"
            "    return None\n"
        )

        self.assertEqual(len(visitor.violations), 1)

    def test_detects_generation_call_in_top_level_conditional(self) -> None:
        visitor = inspect_source(
            "if True:\n"
            "    generate_music()\n"
        )

        self.assertEqual(len(visitor.violations), 1)

    def test_allows_sensitive_calls_inside_function_body(self) -> None:
        visitor = inspect_source(
            "def execute():\n"
            "    worker.spawn()\n"
            "    generate_music()\n"
            "    ensure_model_downloaded()\n"
        )

        self.assertEqual(visitor.violations, [])
        self.assertEqual(len(visitor.safe_calls), 3)

    def test_detects_top_level_modal_deploy_shell_command(self) -> None:
        visitor = inspect_source(
            'import os\n'
            'os.system("modal deploy infra/modal/sonora_staging.py")\n'
        )

        self.assertEqual(len(visitor.violations), 1)

    def test_allows_modal_deploy_command_only_inside_function_body(self) -> None:
        visitor = inspect_source(
            "def explicitly_called_later():\n"
            '    import os\n'
            '    os.system("modal deploy app.py")\n'
        )

        self.assertEqual(visitor.violations, [])
        self.assertEqual(len(visitor.safe_calls), 1)


if __name__ == "__main__":
    unittest.main()
