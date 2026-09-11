"""Detect a repository's own local development command without running it."""

import json
from pathlib import Path
import re


_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
)
_COMPOSE_FILES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)
_TARGET_NAMES = ("dev", "serve")


def _root_path(value):
    try:
        root = Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return None
    return root if root.is_dir() else None


def _result(label, argv, kind, setup=None, **extra):
    result = {
        "label": label,
        "argv": list(argv),
        "setup": list(setup or ()),
        "kind": kind,
    }
    result.update(extra)
    return result


def _package(root):
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _node(root, admin):
    package = _package(root)
    if package is None:
        return None
    scripts = package.get("scripts")
    if not isinstance(scripts, dict):
        return None
    script_name = next(
        (name for name in (("admin:dev",) if admin else ("dev", "start"))
         if isinstance(scripts.get(name), str) and scripts[name].strip()),
        None,
    )
    if not script_name:
        return None
    locked_manager = next((manager for filename, manager in _LOCKFILES
                           if (root / filename).is_file()), None)
    manager = locked_manager
    if manager is None:
        declared = package.get("packageManager")
        if isinstance(declared, str):
            manager = next((name for name in ("pnpm", "yarn", "bun", "npm")
                            if re.match(rf"^{re.escape(name)}(?:@|$)", declared)), None)
        manager = manager or "npm"
    setup = []
    modules = root / "node_modules"
    if not modules.is_dir() or modules.is_symlink():
        if manager == "npm":
            install = "ci" if (root / "package-lock.json").is_file() or (
                root / "npm-shrinkwrap.json"
            ).is_file() else "install"
            setup = [["npm", install, "--no-audit", "--no-fund"]]
        elif manager == "pnpm":
            setup = [["pnpm", "install", "--frozen-lockfile"] if locked_manager else
                     ["pnpm", "install"]]
        elif manager == "bun":
            setup = [["bun", "install", "--frozen-lockfile"] if locked_manager else
                     ["bun", "install"]]
        else:
            setup = [["yarn", "install"]]
    return _result(
        f"{manager} {script_name}",
        [manager, "run", script_name],
        "node",
        setup,
        script=scripts[script_name].strip(),
    )


def _compose(root):
    filename = next((name for name in _COMPOSE_FILES if (root / name).is_file()), None)
    if filename is None:
        return None
    return _result(
        "Docker Compose",
        ["docker", "compose", "-f", f"{{root}}/{filename}", "up"],
        "compose",
    )


def _target(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    for name in _TARGET_NAMES:
        if re.search(rf"(?m)^\s*(?:\[[^\]]*\]\s*)?{re.escape(name)}\s*:", text):
            return name
    return None


def _make_or_just(root):
    make = root / "Makefile"
    target = _target(make) if make.is_file() else None
    if target:
        return _result(f"make {target}", ["make", target], "make")
    just = root / "justfile"
    target = _target(just) if just.is_file() else None
    if target:
        return _result(f"just {target}", ["just", target], "just")
    return None


def _procfile_command(path, require_web):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    plain = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            plain.append(line)
            continue
        name, command = line.split(":", 1)
        if name.strip().lower() == "web" and command.strip():
            return command.strip()
    if not require_web and plain:
        return plain[0]
    return None


def _procfile(root):
    path = root / "Procfile.dev"
    command = _procfile_command(path, require_web=False) if path.is_file() else None
    label = "Procfile.dev"
    if command is None:
        path = root / "Procfile"
        command = _procfile_command(path, require_web=True) if path.is_file() else None
        label = "Procfile web"
    if command is None:
        return None
    return _result(label, ["/bin/sh", "-lc", command], "procfile", script=command)


def _requirements_file(root):
    direct = root / "requirements.txt"
    if direct.is_file():
        return direct
    directory = root / "requirements"
    if not directory.is_dir():
        return None
    try:
        candidates = sorted(path for path in directory.glob("*.txt") if path.is_file())
    except OSError:
        return None
    return candidates[0] if candidates else None


def _python_command(root, requirements):
    if requirements or (root / "pyproject.toml").is_file():
        return "{root}/.venv/bin/python"
    if (root / ".venv/bin/python").is_file():
        return "{root}/.venv/bin/python"
    if (root / "venv/bin/python").is_file():
        return "{root}/venv/bin/python"
    return "python3"


def _python_setup(root, requirements):
    if requirements is None or (root / ".venv/bin/python").is_file():
        return []
    relative = requirements.relative_to(root)
    return [
        ["python3", "-m", "venv", "{root}/.venv"],
        ["{root}/.venv/bin/python", "-m", "pip", "install", "-r",
         f"{{root}}/{relative}"],
    ]


def _django(root):
    manage = root / "manage.py"
    if not manage.is_file():
        return None
    requirements = _requirements_file(root)
    python = _python_command(root, requirements)
    return _result(
        "Django",
        [python, "manage.py", "runserver", "127.0.0.1:{port}"],
        "django",
        _python_setup(root, requirements),
    )


def _python_app(root, framework):
    """Find one explicit ``app = Framework(...)`` module at a shallow path."""
    candidates = [root / "main.py", root / "app.py", root / "src/main.py"]
    found = []
    constructor = re.escape(framework)
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        match = re.search(rf"\b([A-Za-z_]\w*)\s*=\s*{constructor}\s*\(", text)
        if match:
            module = ".".join(path.relative_to(root).with_suffix("").parts)
            found.append((module, match.group(1)))
    return found[0] if len(found) == 1 else None


def _python_dependency(root, name):
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    pattern = rf"(?i)(?:^|[\s\"']){re.escape(name)}(?:[\s\"'=<>!~]|$)"
    return bool(re.search(pattern, text))


def _python_web(root, framework):
    if not _python_dependency(root, framework):
        return None
    app = _python_app(root, "FastAPI" if framework == "fastapi" else "Flask")
    if app is None:
        return None
    module, variable = app
    requirements = _requirements_file(root)
    python = _python_command(root, requirements)
    if framework == "fastapi":
        command = ["-m", "uvicorn"]
        argv = [python, *command, f"{module}:{variable}", "--host", "127.0.0.1",
                "--port", "{port}"]
    else:
        command = ["-m", "flask"]
        argv = [python, *command, "--app", f"{module}:{variable}", "run",
                "--host", "127.0.0.1", "--port", "{port}"]
    setup = _python_setup(root, requirements)
    if framework == "fastapi" and setup:
        setup.append([python, "-m", "pip", "install", "uvicorn"])
    return _result(
        "FastAPI" if framework == "fastapi" else "Flask",
        argv,
        framework,
        setup,
    )


def _fastapi(root):
    return _python_web(root, "fastapi")


def _flask(root):
    return _python_web(root, "flask")


def _rails(root):
    if not (root / "bin/rails").is_file():
        return None
    setup = [["bundle", "install"]] if (root / "Gemfile").is_file() else []
    return _result(
        "Rails",
        ["bundle", "exec", "rails", "server", "-b", "127.0.0.1", "-p", "{port}"],
        "rails",
        setup,
    )


def _laravel(root):
    if not (root / "artisan").is_file():
        return None
    setup = [["composer", "install", "--no-interaction"]] if (
        ((root / "composer.lock").is_file() or (root / "composer.json").is_file())
        and not (root / "vendor/autoload.php").is_file()
    ) else []
    return _result(
        "Laravel",
        ["php", "artisan", "serve", "--host=127.0.0.1", "--port={port}"],
        "laravel",
        setup,
    )


def _spring_boot(root):
    candidates = (
        (root / "pom.xml", root / "mvnw", ["spring-boot:run",
                                              "-Dspring-boot.run.arguments=--server.port={port}"]),
        (root / "build.gradle", root / "gradlew", ["bootRun", "--args=--server.port={port}"]),
        (root / "build.gradle.kts", root / "gradlew", ["bootRun", "--args=--server.port={port}"]),
    )
    for build_file, wrapper, arguments in candidates:
        if not build_file.is_file() or not wrapper.is_file():
            continue
        try:
            text = build_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if not re.search(r"(?i)(?:org\.springframework\.boot|spring-boot)", text):
            continue
        tool = "mvnw" if build_file.name == "pom.xml" else "gradlew"
        return _result("Spring Boot", [f"{{root}}/{tool}", *arguments], "spring-boot")
    return None


def _phoenix(root):
    try:
        text = (root / "mix.exs").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if not re.search(r"(?i)(?:\{:phoenix\b|Phoenix\.Endpoint|phoenix_live_view)", text):
        return None
    return _result("Phoenix", ["mix", "phx.server"], "phoenix")


def _dotnet(root):
    try:
        projects = sorted(path for path in root.glob("*.csproj") if path.is_file())
    except OSError:
        return None
    if len(projects) != 1:
        return None
    try:
        text = projects[0].read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if not re.search(r"Microsoft\.NET\.Sdk\.(?:Web|Razor)|Microsoft\.AspNetCore", text,
                    re.IGNORECASE):
        return None
    project = projects[0].name
    return _result(
        "ASP.NET",
        ["dotnet", "run", "--project", f"{{root}}/{project}",
         "--urls", "http://127.0.0.1:{port}"],
        "dotnet",
    )


def _static(root):
    if not (root / "index.html").is_file():
        return None
    return _result(
        "Static files",
        ["python3", "-m", "http.server", "{port}", "--bind", "127.0.0.1"],
        "static",
    )


def _go_entrypoint(root):
    candidates = []
    main = root / "main.go"
    if main.is_file():
        candidates.append((main, "."))
    command_dir = root / "cmd"
    if command_dir.is_dir():
        try:
            for directory in sorted(path for path in command_dir.iterdir() if path.is_dir()):
                entry = directory / "main.go"
                if entry.is_file():
                    candidates.append((entry, f"{{root}}/cmd/{directory.name}"))
        except OSError:
            pass
    for path, argument in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if re.search(r"(?m)^\s*package\s+main\b", text) and re.search(
            r"\bfunc\s+main\s*\(", text
        ):
            return argument
    return None


def _go(root):
    entrypoint = _go_entrypoint(root)
    if entrypoint is None:
        return None
    if not (root / "go.mod").is_file():
        entrypoint = ("{root}/main.go" if entrypoint == "." else
                      f"{entrypoint}/main.go")
    return _result("Go run", ["go", "run", entrypoint], "go")


def _rust_entrypoint(root):
    try:
        text = (root / "Cargo.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    sections = re.split(r"(?m)^\s*\[\[bin\]\]\s*$", text)[1:]
    for section in sections:
        section = re.split(r"(?m)^\s*\[", section, maxsplit=1)[0]
        name = re.search(r"(?m)^\s*name\s*=\s*[\"']([^\"']+)[\"']", section)
        path = re.search(r"(?m)^\s*path\s*=\s*[\"']([^\"']+)[\"']", section)
        entry = root / (path.group(1) if path else "src/main.rs")
        if entry.is_file():
            return name.group(1) if name else ""
    return None


def _rust(root):
    if not (root / "Cargo.toml").is_file():
        return None
    try:
        main = root / "src/main.rs"
        has_main = main.is_file() and bool(re.search(
            r"\bfn\s+main\s*\(", main.read_text(encoding="utf-8")
        ))
    except (OSError, UnicodeError):
        has_main = False
    binary = "" if has_main else _rust_entrypoint(root)
    if not has_main and binary is None:
        return None
    argv = ["cargo", "run"]
    if binary:
        argv += ["--bin", binary]
    return _result("Rust run", argv, "rust")


def detect(root: str, admin=False):
    """Return a repo-authored preview command, or ``None`` for libraries.

    This function only reads repository files. The caller owns cwd, placeholder
    expansion, setup execution, readiness checks, and process cleanup.
    """
    root = _root_path(root)
    if root is None:
        return None
    if admin:
        return _node(root, True)
    for detector in (
        lambda: _node(root, False),
        lambda: _compose(root),
        lambda: _make_or_just(root),
        lambda: _procfile(root),
        lambda: _django(root),
        lambda: _fastapi(root),
        lambda: _flask(root),
        lambda: _rails(root),
        lambda: _laravel(root),
        lambda: _spring_boot(root),
        lambda: _phoenix(root),
        lambda: _dotnet(root),
        lambda: _static(root),
        lambda: _go(root),
        lambda: _rust(root),
    ):
        result = detector()
        if result is not None:
            return result
    return None
