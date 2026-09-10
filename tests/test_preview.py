"""Lifecycle regressions for CI. All process boundaries are mocked."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('preview_under_test', str(Path(__file__).parents[1] / 'bin/preview'))
spec = importlib.util.spec_from_loader(loader.name, loader)
preview = importlib.util.module_from_spec(spec)
loader.exec_module(preview)


class PreviewLifecycleTests(unittest.TestCase):
    def test_stop_is_repeatable_and_publishes_stop_before_signaling(self):
        record = {'process': {'pid': 123}, 'port': 6173}
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
        state = {'/new-tree': {'port': 6173, 'process': {'pid': 456}}}
        with patch.object(preview, '_save_state'), patch.object(preview, '_terminate') as terminate:
            preview._stop(state, ['/old-tree'])
        terminate.assert_not_called()
        self.assertIn('/new-tree', state)

    def test_status_never_changes_saved_state(self):
        record = {'port': 6173, 'process': {'pid': 123}}
        with patch.object(preview, '_load_state', return_value={'/tree': record}), \
             patch.object(preview, '_processes', return_value={}), \
             patch.object(preview, '_members', return_value=[]), \
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
             patch.object(preview, 'dev_command', return_value='vite dev'), \
             patch.object(preview, '_prepare_dependencies', side_effect=RuntimeError('missing')), \
             patch.object(preview, '_stop') as stop, patch.object(preview, '_free_ports') as free:
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                preview._start({}, ['/tree/.preview/admin', '/tree'])
        stop.assert_not_called()
        free.assert_not_called()

    def test_main_failure_removes_both_pair_members(self):
        paths = ['/tree/.preview/admin', '/tree']
        state = {}
        def launch(state, path, port):
            state[path] = {'port': port}
        def stop(state, paths):
            for path in paths:
                state.pop(path, None)
        with patch.object(preview.os.path, 'isdir', return_value=True), \
             patch.object(preview, 'dev_command', return_value='vite dev'), \
             patch.object(preview, '_prepare_dependencies'), \
             patch.object(preview, '_port_holders', return_value={}), \
             patch.object(preview, '_free_ports'), patch.object(preview, '_stop', side_effect=stop), \
             patch.object(preview, '_launch', side_effect=launch), \
             patch.object(preview, '_wait_ready', side_effect=[None, RuntimeError('main failed')]), \
             patch.object(preview, '_log_tail', return_value='main failed'), \
             patch.object(preview, '_save_state'):
            result = preview._start(state, paths)
        self.assertFalse(result['ok'])
        self.assertEqual(result['component'], 'main')
        self.assertNotIn(paths[0], state)
        self.assertNotIn('process', state[paths[1]])

    def test_stop_failure_remains_retryable(self):
        record = {'process': {'pid': 123}, 'port': 6173}
        state = {'/tree': record}
        with patch.object(preview, '_save_state'), patch.object(preview, '_processes', return_value={}), \
             patch.object(preview, '_members', return_value=[{'pid': 123}]), \
             patch.object(preview, '_terminate', side_effect=RuntimeError('still alive')):
            with self.assertRaisesRegex(RuntimeError, 'still alive'):
                preview._stop(state, ['/tree'])
        self.assertEqual(state['/tree']['process']['pid'], 123)


if __name__ == '__main__':
    unittest.main()
