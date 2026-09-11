"""Pure project detection tests; commands are inspected and never executed."""

import importlib.util
import inspect
from pathlib import Path
import json
import tempfile
import unittest


MODULE_PATH = Path(__file__).parents[1] / "lib/project.py"
SPEC = importlib.util.spec_from_file_location("project_under_test", MODULE_PATH)
project = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(project)


class ProjectDetectionTests(unittest.TestCase):
    def _file(self, root, name, text=""):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_node_dev_uses_lockfile_and_keeps_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({"scripts": {"dev": "node server.js"}}))
            self._file(root, "pnpm-lock.yaml", "lockfileVersion: 9\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "node")
            self.assertEqual(result["script"], "node server.js")
            self.assertEqual(result["argv"][:3], ["pnpm", "run", "dev"])
            self.assertEqual(result["setup"][0][:2], ["pnpm", "install"])

    def test_node_detection_has_no_app_specific_mode_or_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({"scripts": {"dev": "node server.js"}}))
            result = project.detect(str(root))
            self.assertEqual(tuple(inspect.signature(project.detect).parameters), ("root",))
            serialized = json.dumps(result).casefold()
            for marker in ("admin", "oauth", "firebase", "healthtree", "samson", ":6173", ":3200"):
                self.assertNotIn(marker, serialized)

    def test_node_uses_standard_dev_script_when_other_scripts_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({
                "scripts": {"admin:dev": "node private.js", "dev": "node server.js"},
            }))
            result = project.detect(str(root))
            self.assertEqual(result["argv"], ["npm", "run", "dev"])
            self.assertEqual(result["script"], "node server.js")

    def test_node_services_include_generic_namespaced_dev_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({
                "scripts": {
                    "dev": "node web.js",
                    "api:dev": "node api.js",
                    "api:emulator": "node api-emulator.js",
                    "dev:watch": "node watch.js",
                    "api:test": "node test.js",
                },
            }))
            services = project.detect_services(str(root))
            self.assertEqual([service["service"] for service in services], ["dev", "api:emulator"])
            self.assertEqual([service["service_label"] for service in services], ["dev", "api"])
            self.assertEqual(services[1]["argv"], ["npm", "run", "api:emulator"])
            self.assertEqual(services[1]["script"], "node api-emulator.js")

    def test_namespaced_dev_script_can_be_the_only_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({
                "scripts": {"docs:dev": "python3 -m http.server"},
            }))
            services = project.detect_services(str(root))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]["service_label"], "docs")
            self.assertIsNone(project.detect(str(root)))

    def test_namespaced_emulator_script_can_be_the_only_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({
                "scripts": {"docs:emulator": "python3 -m http.server"},
            }))
            services = project.detect_services(str(root))
            self.assertEqual(len(services), 1)
            self.assertEqual(services[0]["service"], "docs:emulator")
            self.assertEqual(services[0]["service_label"], "docs")

    def test_node_defaults_to_npm_without_a_lockfile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "package.json", json.dumps({"scripts": {"start": "node server.js"}}))
            result = project.detect(str(root))
            self.assertEqual(result["argv"], ["npm", "run", "start"])

    def test_compose_is_used_when_no_node_server_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "compose.yaml", "services: {}\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "compose")
            self.assertEqual(result["argv"][:3], ["docker", "compose", "-f"])

    def test_make_target_precedes_framework_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "Makefile", "dev:\n\tgo run .\n")
            self._file(root, "main.go", "package main\nfunc main() {}\n")
            self._file(root, "go.mod", "module example.test/app\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "make")
            self.assertEqual(result["argv"], ["make", "dev"])

    def test_procfile_command_is_explicit_shell_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "Procfile.dev", "web: bundle exec rails s -p $PORT\nworker: nope\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "procfile")
            self.assertEqual(result["argv"][:2], ["/bin/sh", "-lc"])
            self.assertEqual(result["script"], "bundle exec rails s -p $PORT")

    def test_django_requirements_produce_venv_and_pip_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "manage.py", "#!/usr/bin/env python3\n")
            self._file(root, "requirements.txt", "Django\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "django")
            self.assertEqual(result["argv"][-1], "127.0.0.1:{port}")
            self.assertEqual(result["setup"][1][-2:], ["-r", "{root}/requirements.txt"])

    def test_python_web_apps_need_declared_dependency_and_explicit_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "pyproject.toml", 'dependencies = ["fastapi>=0.1"]\n')
            self._file(root, "main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "fastapi")
            self.assertEqual(result["argv"][:4], ["{root}/.venv/bin/python", "-m", "uvicorn", "main:app"])
            self._file(root, "app.py", "from fastapi import FastAPI\napp = FastAPI()\n")
            self.assertIsNone(project.detect(str(root)))

    def test_spring_boot_requires_framework_marker_and_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "pom.xml", "<artifactId>spring-boot-starter-web</artifactId>\n")
            self.assertIsNone(project.detect(str(root)))
            self._file(root, "mvnw", "#!/bin/sh\n")
            result = project.detect(str(root))
            self.assertEqual(result["kind"], "spring-boot")
            self.assertIn("{port}", result["argv"][-1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "build.gradle", "id 'org.springframework.boot' version '3.0.0'\n")
            self._file(root, "gradlew", "#!/bin/sh\n")
            result = project.detect(str(root))
            self.assertEqual(result["argv"][0], "{root}/gradlew")
            self.assertEqual(result["argv"][1], "bootRun")

    def test_phoenix_requires_mix_framework_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "mix.exs", "defp deps, do: [{:phoenix, \"~> 1.7\"}]\n")
            result = project.detect(str(root))
            self.assertEqual(result, {
                "label": "Phoenix",
                "argv": ["mix", "phx.server"],
                "setup": [],
                "kind": "phoenix",
            })

    def test_rails_laravel_and_dotnet_have_native_commands(self):
        cases = (
            ("bin/rails", "Gemfile", "rails", ["bundle", "exec"]),
            ("artisan", "composer.lock", "laravel", ["php", "artisan"]),
            ("app.csproj", None, "dotnet", ["dotnet", "run"]),
        )
        for marker, setup_marker, kind, argv_prefix in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._file(
                    root,
                    marker,
                    '<Project Sdk="Microsoft.NET.Sdk.Web" />\n' if kind == "dotnet" else "",
                )
                if setup_marker:
                    self._file(root, setup_marker)
                result = project.detect(str(root))
                self.assertEqual(result["kind"], kind)
                self.assertEqual(result["argv"][:len(argv_prefix)], argv_prefix)

    def test_go_and_rust_require_actual_main_entrypoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "go.mod", "module example.test/app\n")
            self._file(root, "main.go", "package app\n")
            self.assertIsNone(project.detect(str(root)))
            self._file(root, "main.go", "package main\nfunc main() {}\n")
            self.assertEqual(project.detect(str(root))["label"], "Go run")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._file(root, "Cargo.toml", "[package]\nname='app'\n")
            self._file(root, "src/lib.rs", "pub fn run() {}\n")
            self.assertIsNone(project.detect(str(root)))
            self._file(root, "src/main.rs", "fn main() {}\n")
            self.assertEqual(project.detect(str(root))["kind"], "rust")

    def test_static_is_last_resort_and_empty_library_is_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(project.detect(str(root)))
            self._file(root, "index.html", "<h1>app</h1>\n")
            self.assertEqual(project.detect(str(root))["kind"], "static")


if __name__ == "__main__":
    unittest.main()
