"""Package upgrades and initialization boundaries; no task or model calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "framework"))
import native_desktop as native


class PackageResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def package(self, version, status='Ok', present=True):
        root = self.root / version
        if present:
            server = root / native.SERVER_RELATIVE
            server.parent.mkdir(parents=True, exist_ok=True)
            server.write_text('// test entry', encoding='utf-8')
        return dict(Name='OpenAI.Codex', Version=version, InstallLocation=str(root),
                    Status=status, IsFramework=False, IsResourcePackage=False)

    def resolve(self, packages, running=()):
        with patch.object(native, 'installed_packages', return_value={
                'packages': packages, 'running_paths': list(running)}):
            return native.resolve_desktop_server()

    def test_removed_old_package_and_unlistable_windowsapps(self):
        old = self.package('26.915.3509.0', present=False)
        new = self.package('26.915.4065.0')
        with patch.object(Path, 'glob', side_effect=PermissionError('enumeration denied')):
            self.assertEqual(self.resolve([old, new]), Path(new['InstallLocation']) / native.SERVER_RELATIVE)

    def test_numeric_version_order(self):
        old, new = self.package('26.9.9.0'), self.package('26.10.1.0')
        self.assertEqual(self.resolve([old, new]), Path(new['InstallLocation']) / native.SERVER_RELATIVE)

    def test_running_registered_desktop_preferred(self):
        active, updated = self.package('26.9.9.0'), self.package('26.10.1.0')
        running = [str(Path(active['InstallLocation']) / 'app/Codex.exe')]
        self.assertEqual(self.resolve([active, updated], running), Path(active['InstallLocation']) / native.SERVER_RELATIVE)

    def test_invalid_package_and_missing_entry_are_not_selected(self):
        bad = self.package('26.10.1.0', status='Modified')
        missing = self.package('26.10.2.0', present=False)
        with self.assertRaisesRegex(RuntimeError, 'phase=package_resolve'):
            self.resolve([bad, missing])

    def test_empty_packages_report_failure(self):
        with self.assertRaisesRegex(RuntimeError, 'phase=package_resolve'):
            self.resolve([])

    def test_query_timeout_has_stage(self):
        with patch.object(native.subprocess, 'run', side_effect=subprocess.TimeoutExpired('powershell', 10)):
            with self.assertRaisesRegex(RuntimeError, 'phase=package_query'):
                native.installed_packages()

    def test_query_exit_and_malformed_metadata(self):
        with patch.object(native.subprocess, 'run', return_value=Mock(returncode=1, stderr='query failed')):
            with self.assertRaisesRegex(RuntimeError, 'exit_code=1'):
                native.installed_packages()
        with patch.object(native.subprocess, 'run', return_value=Mock(returncode=0, stdout='not json')):
            with self.assertRaisesRegex(RuntimeError, 'invalid JSON'):
                native.installed_packages()

    def test_entry_removed_during_initialization_relocates_once(self):
        old = self.root / 'gone.mjs'
        new = self.root / 'new.mjs'
        new.write_text('// new', encoding='utf-8')
        calls = []
        def connect(client):
            calls.append(client.server_path)
            if client.server_path == old:
                raise FileNotFoundError(str(old))
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'test', 'CODEX_APP_TOOLS_PIPE_PATH': 'test'}), \
                patch.object(native, 'resolve_desktop_server', side_effect=[old, new]) as resolve, \
                patch.object(native.Desktop, '_connect', connect):
            client = native.Desktop('test')
            self.assertEqual(client.server_path, new)
            self.assertEqual(resolve.call_count, 2)
            self.assertEqual(calls, [old, new])

    def test_protocol_failure_with_existing_entry_never_retries(self):
        path = self.root / 'valid.mjs'
        path.write_text('// valid', encoding='utf-8')
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'test', 'CODEX_APP_TOOLS_PIPE_PATH': 'test'}), \
                patch.object(native, 'resolve_desktop_server', return_value=path) as resolve, \
                patch.object(native.Desktop, '_connect', side_effect=RuntimeError('protocol rejected')):
            with self.assertRaisesRegex(RuntimeError, 'desktop_initialize.*protocol rejected'):
                native.Desktop('test')
            self.assertEqual(resolve.call_count, 1)

    def test_upgrade_retry_is_bounded(self):
        path = self.root / 'gone.mjs'
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'test', 'CODEX_APP_TOOLS_PIPE_PATH': 'test'}), \
                patch.object(native, 'resolve_desktop_server', return_value=path) as resolve, \
                patch.object(native.Desktop, '_connect', side_effect=FileNotFoundError('gone')):
            with self.assertRaisesRegex(RuntimeError, 'desktop_initialize'):
                native.Desktop('test')
            self.assertEqual(resolve.call_count, 2)


if __name__ == '__main__':
    unittest.main()
