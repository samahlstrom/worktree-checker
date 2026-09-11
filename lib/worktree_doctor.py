"""Worktree Doctor: one repair path used by Start and the standalone command.

The dependency check is the original shared dep-health.sh from worktree-doctor.
Repair is scoped to the selected project; it never provisions test environments.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def primary_checkout(path):
    """Owning primary checkout via git-common-dir -- same mechanism as
    worktree-setup's local-env source. None when path is not a worktree."""
    root = os.path.realpath(path)
    try:
        r = subprocess.run(
            ["git", "-C", root, "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    common = (r.stdout or "").strip().rstrip("/")
    if r.returncode != 0 or not common:
        return None
    if os.path.basename(common) != ".git":
        return None
    return os.path.dirname(common)


def health(root):
    """Use Doctor's shared npm-tree and host-native binary check."""
    root = Path(root)
    if not (root / 'package.json').is_file():
        return {'ok': True}
    package = json.loads((root / 'package.json').read_text())
    if not any(package.get(key) for key in ('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'workspaces')):
        return {'ok': True}
    if (root / '.pnp.cjs').is_file():
        return {'ok': True}
    modules = root / 'node_modules'
    if not modules.is_dir() or modules.is_symlink():
        return {'ok': False, 'error': 'Packages need setup.'}
    check = Path(__file__).with_name('dep-health.sh')
    try:
        result = subprocess.run(['/bin/bash', '-c', 'source "$1"; modules_are_healthy "$2"',
                                 '--', str(check), str(modules)], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': 'Package check timed out.'}
    return {'ok': result.returncode == 0, 'error': 'Packages need repair.' if result.returncode else ''}


def repair_local_files(root):
    root = Path(root)
    primary = primary_checkout(root)
    if primary and Path(primary) != root:
        for source in Path(primary).glob('.env*'):
            target = root / source.name
            if not source.is_file() or target.exists():
                continue
            ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '--no-index', source.name],
                                     capture_output=True, timeout=5)
            if ignored.returncode == 0:
                shutil.copy2(source, target)
    for name in ('.svelte-kit', '.svelte-kit-admin'):
        generated = root / name
        if generated.is_symlink() and root not in generated.resolve().parents:
            generated.unlink()


def prepare(root, project, run_setup):
    """Repair this project before launch, preserving its source and lockfile."""
    root = Path(root)
    log_dir = root / '.preview'
    log_dir.mkdir(parents=True, exist_ok=True)
    repair_local_files(root)
    if project['kind'] != 'node':
        for command in project['setup']:
            run_setup([arg.replace('{root}', str(root)) for arg in command], root, log_dir)
        return
    if health(root)['ok']:
        return
    primary = primary_checkout(root)
    donor = Path(primary) if primary else None
    lock = next((name for name in ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml',
                                  'yarn.lock', 'bun.lock', 'bun.lockb') if (root / name).is_file()), None)
    compatible = bool(donor and donor != root and lock and (donor / lock).is_file()
                      and (root / lock).read_bytes() == (donor / lock).read_bytes())
    if compatible and sys.platform == 'darwin' and health(donor)['ok']:
        temporary = Path(tempfile.mkdtemp(prefix='.dependencies-', dir=log_dir))
        try:
            result = subprocess.run(['cp', '-cR', str(donor / 'node_modules'), str(temporary / 'node_modules')],
                                    capture_output=True, timeout=120)
            if result.returncode == 0:
                modules = root / 'node_modules'
                if modules.is_symlink():
                    modules.unlink()
                elif modules.exists():
                    shutil.rmtree(modules)
                (temporary / 'node_modules').replace(modules)
                if health(root)['ok']:
                    return
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
    modules = root / 'node_modules'
    if modules.is_symlink():
        modules.unlink()
    manager = project['argv'][0]
    if manager == 'npm':
        command = ['npm', 'ci' if lock in ('package-lock.json', 'npm-shrinkwrap.json') else 'install', '--no-audit', '--no-fund']
    elif manager in ('pnpm', 'bun'):
        command = [manager, 'install'] + (['--frozen-lockfile'] if lock else [])
    else:
        command = [manager, 'install']
    run_setup(command, root, log_dir)
    if not health(root)['ok']:
        raise RuntimeError('Package repair did not finish. The setup log has the details.')
