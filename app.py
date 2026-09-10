#!/usr/bin/env python3
"""Worktree Checker: open the board; the installed app runs it at login."""
import os
from pathlib import Path
import plistlib
import runpy
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:7777'
LABEL = 'local.worktree-checker'


def configure_path():
    home = Path.home()
    candidates = [home / '.local/bin', Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
    for pattern in ('.nvm/versions/node/*/bin', '.local/share/mise/installs/node/*/bin',
                    '.asdf/installs/nodejs/*/bin', '.local/share/fnm/node-versions/*/installation/bin'):
        candidates.extend(sorted(home.glob(pattern), reverse=True))
    candidates.append(ROOT / 'runtime/bin')
    os.environ['PATH'] = os.pathsep.join(map(str, candidates)) + os.pathsep + os.environ.get('PATH', '/usr/bin:/bin')


def install_login_service():
    logs = Path.home() / '.local/state/worktree-preview-board'
    logs.mkdir(parents=True, exist_ok=True)
    agent = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    agent.parent.mkdir(parents=True, exist_ok=True)
    settings = dict(Label=LABEL, ProgramArguments=[sys.executable, '--serve'],
                    RunAtLoad=True, KeepAlive=True, ThrottleInterval=5,
                    WorkingDirectory=str(Path.home()),
                    StandardOutPath=str(logs / 'board.log'), StandardErrorPath=str(logs / 'board.log'))
    previous = plistlib.loads(agent.read_bytes()) if agent.exists() else None
    if previous != settings:
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)
        agent.write_bytes(plistlib.dumps(settings))
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(agent)], capture_output=True)
    subprocess.run(['launchctl', 'kickstart', f'gui/{os.getuid()}/{LABEL}'], capture_output=True)


def main():
    configure_path()
    serve = '--serve' in sys.argv
    if serve:
        sys.argv = [str(ROOT / 'bin/board')]
        runpy.run_path(sys.argv[0], run_name='__main__')
        return
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        install_login_service()
    else:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(60):
        try:
            with urllib.request.urlopen(URL, timeout=1):
                webbrowser.open(URL)
                return
        except OSError:
            time.sleep(.25)
    raise RuntimeError('The board could not open. See ~/.local/state/worktree-preview-board/board.log.')


if __name__ == '__main__':
    main()
