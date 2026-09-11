"""Pure runtime ownership tests; Docker and lsof are mocked, never invoked."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "bin/preview"
LOADER = importlib.machinery.SourceFileLoader("preview_runtime_under_test", str(MODULE_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class ComposeRuntimeTests(unittest.TestCase):
    def _inspect_result(self):
        project = "worktree-checker-test"
        return [
            {
                "Id": "app-id",
                "State": {"Running": True},
                "Config": {"Labels": {"com.docker.compose.project": project}},
                "NetworkSettings": {"Ports": {
                    "4000/tcp": [{"HostPort": "4400"}],
                    "5432/tcp": [{"HostPort": "4543"}],
                    "8080/tcp": None,
                }},
            },
            {
                "Id": "foreign-id",
                "State": {"Running": True},
                "Config": {"Labels": {"com.docker.compose.project": "other-project"}},
                "NetworkSettings": {"Ports": {"9000/tcp": [{"HostPort": "4900"}]}},
            },
        ]

    def test_container_identity_requires_exact_project_label(self):
        project = "worktree-checker-test"
        responses = [
            SimpleNamespace(returncode=0, stdout="app-id\nforeign-id\n"),
            SimpleNamespace(returncode=0, stdout=json.dumps(self._inspect_result())),
        ]
        with patch.object(preview.subprocess, "run", side_effect=responses) as run:
            containers = preview._containers({"compose_project": project})
        self.assertEqual([item["Id"] for item in containers], ["app-id"])
        self.assertIn(f"label=com.docker.compose.project={project}", run.call_args_list[0].args[0])

    def test_container_ports_keep_numeric_host_ports_for_owned_project(self):
        project = "worktree-checker-test"
        responses = [
            SimpleNamespace(returncode=0, stdout="app-id\n"),
            SimpleNamespace(returncode=0, stdout=json.dumps(self._inspect_result()[:1])),
        ]
        with patch.object(preview.subprocess, "run", side_effect=responses):
            ports = preview._container_ports({"compose_project": project})
        self.assertEqual(ports, [4400, 4543])


if __name__ == "__main__":
    unittest.main()
