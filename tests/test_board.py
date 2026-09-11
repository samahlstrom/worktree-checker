"""Generic board controls; importing the board starts no services."""

import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "lib"))
loader = importlib.machinery.SourceFileLoader("board_under_test", str(ROOT / "bin/board"))
spec = importlib.util.spec_from_loader(loader.name, loader)
board = importlib.util.module_from_spec(spec)
loader.exec_module(board)


class BoardControlTests(unittest.TestCase):
    def _row(self, running=False):
        return {
            "name": "feature-widget",
            "repo": "widget",
            "owner": "Example",
            "project": "Example/widget",
            "branch": "feature/widget",
            "issue_id": "",
            "commit_date": "",
            "path": "/tmp/feature-widget",
            "running": running,
            "crashed": False,
            "log_tail": "",
            "message": "",
            "port": 43123 if running else None,
            "startable": True,
            "head": "abc123",
            "served_revision": "",
            "pr_number": None,
            "pr_url": "",
            "pr_title": "",
            "pr_state": "",
            "pr_label": "",
        }

    def test_stopped_row_has_generic_start_without_app_controls(self):
        rendered = board._row_html(self._row())
        self.assertIn('class="btn start"', rendered)
        self.assertNotIn('class="btn stop"', rendered)
        self._assert_no_app_specific_output(rendered)

    def test_running_row_has_generic_open_and_stop_without_app_controls(self):
        rendered = board._row_html(self._row(running=True))
        self.assertIn('class="btn stop"', rendered)
        self.assertIn('>open</a>', rendered)
        self._assert_no_app_specific_output(rendered)

    def test_multi_service_row_has_independent_generic_controls(self):
        row = self._row(running=True)
        row['services'] = [
            {
                'name': 'dev', 'path': row['path'], 'running': True,
                'crashed': False, 'port': 43123, 'message': '', 'log_tail': '',
                'served_revision': 'abc123',
            },
            {
                'name': 'api', 'path': row['path'] + '/.preview/services/api%3Adev',
                'running': False, 'crashed': False, 'port': None,
                'message': '', 'log_tail': '', 'served_revision': '',
            },
        ]
        rendered = board._row_html(row)
        self.assertIn('open dev :43123', rendered)
        self.assertIn('stop dev', rendered)
        self.assertIn('start api', rendered)
        self.assertNotIn('stop api', rendered)
        self._assert_no_app_specific_output(rendered)

    def test_multi_service_running_banner_names_each_service(self):
        row = self._row(running=True)
        row['services'] = [
            {
                'name': 'dev', 'path': row['path'], 'running': True,
                'crashed': False, 'port': 43123,
            },
            {
                'name': 'api', 'path': row['path'] + '/.preview/services/api%3Adev',
                'running': True, 'crashed': False, 'port': 43124,
            },
        ]
        rendered = board._running_banner([row])
        self.assertIn('feature-widget / dev', rendered)
        self.assertIn('feature-widget / api', rendered)
        self.assertEqual(rendered.count('class="btn stop"'), 2)
        self._assert_no_app_specific_output(rendered)

    def test_saved_path_without_a_server_definition_is_stopped(self):
        with patch.object(board.preview, "_load_state", return_value={"/old-tree": {}}), \
             patch.object(board.preview, "dev_command", return_value=None), \
             patch.object(board.preview, "stop") as stop, \
             patch.object(board, "_cached_repo_roots", return_value=[]):
            board.refresh_running_previews(now=0)
        stop.assert_called_once_with("/old-tree")

    def _assert_no_app_specific_output(self, rendered):
        for marker in (
            "admin",
            "impersonate",
            "oauth",
            "firebase",
            "healthtree",
            "samson",
            "start-pair",
            "stop-pair",
            ":6173",
            ":3200",
        ):
            self.assertNotIn(marker, rendered.casefold())


if __name__ == "__main__":
    unittest.main()
