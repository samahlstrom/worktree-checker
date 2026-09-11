"""Doctor's repair decisions, with external commands mocked."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parents[1] / 'lib'))
import project
import worktree_doctor as doctor


class DoctorTests(unittest.TestCase):
    def test_builtin_node_server_needs_no_dependency_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({'scripts': {'dev': 'node server.js'}}))
            run = Mock()
            doctor.prepare(root, project.detect(root), run)
            run.assert_not_called()

    def test_missing_packages_use_the_detected_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({'scripts': {'dev': 'vite'}, 'dependencies': {'vite': '*'}, 'packageManager': 'pnpm@10.0.0'}))
            run = Mock()
            with patch.object(doctor, 'health', side_effect=[{'ok': False}, {'ok': True}]):
                doctor.prepare(root, project.detect(root), run)
            self.assertEqual(run.call_args.args[0], ['pnpm', 'install'])

    def test_timeout_is_reported_as_unhealthy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text('{"dependencies":{"vite":"*"}}')
            (root / 'node_modules').mkdir()
            with patch.object(doctor.subprocess, 'run', side_effect=subprocess.TimeoutExpired('npm', 30)):
                self.assertFalse(doctor.health(root)['ok'])

    def test_pyproject_only_app_has_install_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pyproject.toml').write_text('[project]\nname="sample"\nversion="1"\ndependencies=["fastapi"]\n')
            (root / 'main.py').write_text('from fastapi import FastAPI\napp = FastAPI()\n')
            plan = project.detect(root)
            self.assertIn(['{root}/.venv/bin/python', '-m', 'pip', 'install', '-e', '{root}'], plan['setup'])
