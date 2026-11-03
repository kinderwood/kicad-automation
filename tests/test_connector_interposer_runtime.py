"""Runtime-relative worker interpreter discovery and validation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from plugins.connector_interposer_workflow.controller import NativeProcessBackend


class InterpreterDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def runtime(self, module_path, executable, prefix, version='9.0.0'):
        module = SimpleNamespace(__file__=str(module_path), Version=lambda: version)
        return mock.patch.multiple(
            sys, executable=str(executable), prefix=str(prefix), exec_prefix=str(prefix)
        ), mock.patch.dict(sys.modules, {'pcbnew': module})

    @staticmethod
    def probe(version='9.0.0', error=None, calls=None):
        def run(command, **kwargs):
            if calls is not None:
                calls.append((list(command), dict(kwargs)))
            result = Path(command[-1])
            payload = {'ok': error is None, 'version': version}
            if error is not None:
                payload['error'] = error
            result.write_text(json.dumps(payload), encoding='utf-8')
            return subprocess.CompletedProcess(command, 0 if error is None else 3, '', '')
        return run

    def test_windows_finds_python_exe_beside_pcbnew_in_nondefault_install(self):
        binary = self.root / 'CompanyTools/KiCad-Custom/bin'
        module_path = binary / 'Lib/site-packages/pcbnew.py'
        module_path.parent.mkdir(parents=True)
        module_path.touch()
        editor = binary / 'pcbnew.exe'
        interpreter = binary / 'python.exe'
        editor.touch()
        interpreter.touch()
        calls = []
        patches = self.runtime(module_path, editor, binary)
        with patches[0], patches[1], mock.patch('subprocess.run', self.probe(calls=calls)):
            backend = NativeProcessBackend()
        self.assertEqual(backend.python, [str(interpreter)])
        self.assertEqual(calls[0][0][0], str(interpreter))

    def test_linux_finds_versioned_interpreter_under_prefix_bin(self):
        prefix = self.root / 'usr'
        interpreter = prefix / 'bin' / f'python{sys.version_info.major}.{sys.version_info.minor}'
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        module_path = prefix / 'lib/python/site-packages/pcbnew.py'
        editor = prefix / 'bin/pcbnew'
        patches = self.runtime(module_path, editor, prefix)
        with patches[0], patches[1], mock.patch('subprocess.run', self.probe()):
            backend = NativeProcessBackend()
        self.assertEqual(backend.python, [str(interpreter)])

    def test_appimage_keeps_apprun_command(self):
        appdir = self.root / 'KiCad.AppDir'
        runtime = appdir / 'AppRun'
        runtime.parent.mkdir(parents=True)
        runtime.touch()
        module_path = appdir / 'shared/lib/python/site-packages/pcbnew.py'
        editor = appdir / 'usr/bin/pcbnew'
        patches = self.runtime(module_path, editor, appdir / 'usr')
        with patches[0], patches[1], mock.patch('subprocess.run', self.probe(version='10.0.8')):
            sys.modules['pcbnew'].Version = lambda: '10.0.8'
            backend = NativeProcessBackend()
        self.assertEqual(backend.python, [str(runtime), f'python{sys.version_info.major}.{sys.version_info.minor}'])

    def test_explicit_override_is_preserved_and_validated(self):
        interpreter = self.root / 'explicit/python.exe'
        interpreter.parent.mkdir()
        interpreter.touch()
        module_path = self.root / 'runtime/pcbnew.py'
        patches = self.runtime(module_path, self.root / 'pcbnew.exe', self.root / 'missing')
        calls = []
        with patches[0], patches[1], mock.patch('subprocess.run', self.probe(calls=calls)):
            backend = NativeProcessBackend(python=interpreter)
        self.assertEqual(backend.python, [str(interpreter)])
        self.assertEqual(calls[0][0][0], str(interpreter))

    def test_missing_runtime_does_not_search_path_and_reports_locations(self):
        install = self.root / 'missing-install/bin'
        module_path = install / 'Lib/site-packages/pcbnew.py'
        editor = install / 'pcbnew.exe'
        patches = self.runtime(module_path, editor, install)
        with patches[0], patches[1], mock.patch('subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'System PATH interpreters are not selected automatically') as raised:
                NativeProcessBackend()
        run.assert_not_called()
        self.assertIn(str(install / 'python.exe'), str(raised.exception))

    def test_mismatched_runtime_is_rejected_with_both_versions(self):
        binary = self.root / 'KiCad/bin'
        binary.mkdir(parents=True)
        interpreter = binary / 'python.exe'
        interpreter.touch()
        patches = self.runtime(binary / 'Lib/site-packages/pcbnew.py', binary / 'pcbnew.exe', binary)
        with patches[0], patches[1], mock.patch('subprocess.run', self.probe(version='10.0.8')):
            with self.assertRaises(RuntimeError) as raised:
                NativeProcessBackend()
        message = str(raised.exception)
        self.assertIn('reports KiCad 10.0.8', message)
        self.assertIn('active editor is KiCad 9.0.0', message)

    def test_invalid_explicit_runtime_reports_pcbnew_import_failure(self):
        interpreter = self.root / 'broken/python.exe'
        interpreter.parent.mkdir()
        interpreter.touch()
        patches = self.runtime(self.root / 'runtime/pcbnew.py', self.root / 'pcbnew.exe', self.root)
        with patches[0], patches[1], mock.patch('subprocess.run', self.probe(error='No module named pcbnew')):
            with self.assertRaisesRegex(RuntimeError, 'No module named pcbnew'):
                NativeProcessBackend(python=interpreter)

    def test_windows_subprocesses_do_not_create_a_console(self):
        fake_subprocess = SimpleNamespace(CREATE_NO_WINDOW=0x08000000)
        fake_os = SimpleNamespace(name='nt')
        self.assertEqual(NativeProcessBackend._subprocess_options(fake_subprocess, fake_os),
                         {'creationflags': 0x08000000})


if __name__ == '__main__':
    unittest.main()
