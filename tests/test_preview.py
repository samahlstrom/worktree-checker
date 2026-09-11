"""Lifecycle regressions for CI. All process boundaries are mocked."""
import importlib.machinery
import importlib.util
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('preview_under_test', str(Path(__file__).parents[1] / 'bin/preview'))
spec = importlib.util.spec_from_loader(loader.name, loader)
preview = importlib.util.module_from_spec(spec)
loader.exec_module(preview)


class PreviewLifecycleTests(unittest.TestCase):
    def test_namespaced_node_services_get_independent_component_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({
                'scripts': {'dev': 'node web.js', 'api:dev': 'node api.js'},
            }))
            services = preview.service_definitions(root)
            self.assertEqual([service['name'] for service in services], ['dev', 'api'])
            self.assertEqual(services[0]['path'], str(root.resolve()))
            self.assertIn('/.preview/services/api%3Adev', services[1]['path'])
            self.assertEqual(preview._runtime_root(services[1]['path']), str(root.resolve()))
            self.assertEqual(preview.dev_command(services[1]['path']), 'node api.js')

    def test_namespaced_service_builds_its_own_package_script_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({
                'scripts': {'dev': 'node web.js', 'api:dev': 'node api.js'},
            }))
            component = preview.service_definitions(root)[1]['path']
            argv, _ = preview._build_argv_env('node api.js', 43210, path=component)
            self.assertEqual(argv, ['npm', 'run', 'api:dev'])

    def test_stop_is_repeatable_and_publishes_stop_before_signaling(self):
        record = {'process': {'pid': 123}, 'port': 43123}
        state = {'/tree': record}
        with patch.object(preview, '_save_state') as save, \
             patch.object(preview, '_processes', return_value={}), \
             patch.object(preview, '_members', return_value=[{'pid': 123}]), \
             patch.object(preview, '_terminate') as terminate:
            terminate.side_effect = lambda *args, **kwargs: self.assertNotIn('/tree', state)
            preview._stop(state, ['/tree'])
            preview._stop(state, ['/tree'])
        self.assertEqual(state, {})
        terminate.assert_called_once()
        self.assertTrue(save.called)

    def test_stale_stop_does_not_stop_new_worktree(self):
        state = {'/new-tree': {'port': 43123, 'process': {'pid': 456}}}
        with patch.object(preview, '_save_state'), patch.object(preview, '_terminate') as terminate:
            preview._stop(state, ['/old-tree'])
        terminate.assert_not_called()
        self.assertIn('/new-tree', state)

    def test_status_never_changes_saved_state(self):
        record = {'port': 43123, 'process': {'pid': 123}}
        with patch.object(preview, '_load_state', return_value={'/tree': record}), \
             patch.object(preview, '_processes', return_value={}), \
             patch.object(preview, '_members', return_value=[]), \
             patch.object(preview, '_listeners', return_value=set()), \
             patch.object(preview, '_log_tail', return_value='exited'), \
             patch.object(preview, '_save_state') as save:
            self.assertTrue(preview.status('/tree')['crashed'])
        save.assert_not_called()

    def test_reused_pid_does_not_authorize_signaling(self):
        saved = dict(pid=123, group=123, uid=501, birth='old')
        current = dict(saved, birth='new')
        self.assertEqual(preview._members({'process': saved}, {123: current}), [])

    def test_missing_dependencies_leave_current_preview_untouched(self):
        with patch.object(preview.os.path, 'isdir', return_value=True), \
             patch.object(preview, 'dev_command', return_value='node server.js'), \
             patch.object(preview.doctor, 'prepare', side_effect=RuntimeError('missing')), \
             patch.object(preview, '_stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                preview._start({}, ['/tree'])
        stop.assert_not_called()

    def test_stop_failure_remains_retryable(self):
        record = {'process': {'pid': 123}, 'port': 43123}
        state = {'/tree': record}
        with patch.object(preview, '_save_state'), patch.object(preview, '_processes', return_value={}), \
             patch.object(preview, '_members', return_value=[{'pid': 123}]), \
             patch.object(preview, '_terminate', side_effect=RuntimeError('still alive')):
            with self.assertRaisesRegex(RuntimeError, 'still alive'):
                preview._stop(state, ['/tree'])
        self.assertEqual(state['/tree']['process']['pid'], 123)


if __name__ == '__main__':
    unittest.main()
