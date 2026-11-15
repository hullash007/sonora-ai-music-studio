"""Offline startup checks at Modal's flattened remote module path."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'infra/modal/sonora_staging.py'
SOURCE_TEXT = SOURCE.read_text()


class StubImage:
    def __init__(self, local_dirs):
        self.local_dirs = local_dirs

    def pip_install(self, *args, **kwargs):
        return self

    apt_install = pip_install
    run_commands = pip_install

    def add_local_dir(self, path, **kwargs):
        self.local_dirs.append((path, kwargs))
        return self


class RemoteImportTests(unittest.TestCase):
    def import_staging(self, filename):
        dirs = []
        definitions = []
        def forbidden(*args, **kwargs):
            raise AssertionError('Remote execution during import')
        def identity_decorator(**kwargs):
            definitions.append(kwargs)
            def decorate(fn):
                fn.spawn = forbidden
                fn.remote = forbidden
                return fn
            return decorate
        modal = types.SimpleNamespace(
            App=lambda name: types.SimpleNamespace(function=identity_decorator),
            Image=types.SimpleNamespace(debian_slim=lambda **kwargs: StubImage(dirs)),
            Volume=types.SimpleNamespace(from_name=lambda *a, **k: object()),
            Dict=types.SimpleNamespace(from_name=lambda *a, **k: object()),
            Secret=types.SimpleNamespace(from_name=lambda *a, **k: object()),
            asgi_app=lambda: lambda fn: fn,
        )
        namespace = {'__file__': filename, '__name__': 'staging_offline_import'}
        saved_path = list(sys.path)
        try:
            with patch.dict(sys.modules, {'modal': modal}):
                exec(compile(SOURCE_TEXT, filename, 'exec'), namespace)
            import_path = str(namespace['BACKEND_IMPORT_DIR'])
            self.assertIn(import_path, sys.path)
        finally:
            sys.path[:] = saved_path
        return namespace, dirs, definitions

    def test_remote_flattened_import_uses_packaged_backend(self):
        with patch.object(Path, 'cwd', return_value=Path('/root')), patch.object(
            Path, 'is_file', lambda p: str(p) == '/opt/sonora/backend/sonora/library.py'
        ):
            namespace, dirs, definitions = self.import_staging('/root/sonora_staging.py')
        self.assertEqual(namespace['APP_NAME'], 'sonora-staging')
        self.assertIsNone(namespace['LOCAL_BACKEND_DIR'])
        self.assertEqual(namespace['BACKEND_IMPORT_DIR'], Path('/opt/sonora/backend'))
        self.assertEqual(dirs, [])
        self.assertEqual(len(definitions), 2)

    def test_missing_source_fails_before_deployment(self):
        with self.assertRaisesRegex(RuntimeError, 'SONORA backend not found'):
            self.import_staging('/missing/sonora_staging.py')

    def test_local_definition_attaches_existing_backend_to_both_images(self):
        namespace, dirs, _ = self.import_staging(str(SOURCE))
        self.assertEqual(len(dirs), 2)
        for path, kwargs in dirs:
            self.assertTrue(Path(path, 'sonora/web.py').is_file())
            self.assertEqual(kwargs['remote_path'], '/opt/sonora/backend')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'backend/sonora').mkdir(parents=True)
            (root / 'backend/sonora/library.py').touch()
            module_path = root / 'infra/modal/sonora_staging.py'
            self.assertEqual(namespace['_find_local_backend_dir'](module_path), root / 'backend')


if __name__ == '__main__':
    unittest.main()
