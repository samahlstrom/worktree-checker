"""Real lightweight start/stop checks, run only in GitHub Actions."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

loader = importlib.machinery.SourceFileLoader('native_preview', str(Path(__file__).parents[1] / 'bin/preview'))
spec = importlib.util.spec_from_loader(loader.name, loader)
preview = importlib.util.module_from_spec(spec)
loader.exec_module(preview)


@unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true', 'Real runtime checks run in GitHub Actions')
class NativeRuntimeTests(unittest.TestCase):
    def exercise(self, files):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as state:
            root = Path(directory)
            for name, text in files.items():
                (root / name).write_text(text)
            with patch.object(preview, 'GLOBAL_STATE_DIR', state):
                try:
                    result = preview.start(str(root))
                    self.assertTrue(result['ok'], result)
                    with urllib.request.urlopen(f"http://localhost:{result['port']}", timeout=5) as response:
                        self.assertEqual(response.status, 200)
                    self.assertTrue(preview.start(str(root))['already_running'])
                    self.assertTrue(preview.stop(str(root))['ok'])
                    self.assertTrue(preview.stop(str(root))['ok'])
                    self.assertFalse(preview.status(str(root))['crashed'])
                    self.assertEqual(preview._listeners(result['port']), set())
                finally:
                    preview.stop(str(root))

    def test_node_without_dependencies_or_lockfile(self):
        self.exercise({
            'package.json': json.dumps({'scripts': {'dev': 'node server.cjs'}}),
            'server.cjs': "require('http').createServer((req,res)=>res.end('node ready')).listen(Number(process.env.PORT),'127.0.0.1');",
        })

    def test_make_python_command_and_actual_port_discovery(self):
        self.exercise({
            'Makefile': 'dev:\n\tpython3 server.py\n',
            'server.py': "from http.server import HTTPServer, SimpleHTTPRequestHandler\nHTTPServer(('127.0.0.1', 0), SimpleHTTPRequestHandler).serve_forever()\n",
        })
