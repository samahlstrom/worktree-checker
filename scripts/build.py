"""Build a self-contained macOS app, including Node/npm. Run only for releases."""
import ast
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
runtime = root / 'runtime'
node = Path(shutil.which('node')).resolve()
(runtime / 'bin').mkdir(parents=True, exist_ok=True)
shutil.copy2(node, runtime / 'bin/node')
npm_root = Path(subprocess.check_output(['npm', 'root', '-g'], text=True).strip()) / 'npm'
shutil.copytree(npm_root, runtime / 'lib/node_modules/npm', dirs_exist_ok=True)
for command in ('npm', 'npx'):
    target = runtime / 'bin' / command
    target.unlink(missing_ok=True)
    target.symlink_to(f'../lib/node_modules/npm/bin/{command}-cli.js')
imports = set()
for path in [*root.glob('bin/*'), *root.glob('lib/*.py'), root / 'app.py']:
    if not path.is_file():
        continue
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
        '--name', 'Worktree Checker', '--osx-bundle-identifier', 'local.worktree-checker']
for folder in ('bin', 'lib', 'assets', 'runtime'):
    args.extend(['--add-data', f'{root / folder}:{folder}'])
for module in sorted(imports):
    args.extend(['--hidden-import', module])
args.append(str(root / 'app.py'))
subprocess.run(args, cwd=root, check=True)
