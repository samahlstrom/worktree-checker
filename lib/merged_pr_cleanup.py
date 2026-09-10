#!/usr/bin/env python3
"""Optional cleanup for linked worktrees whose GitHub PR has merged."""
from dataclasses import dataclass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
import traceback
import uuid


CONFIG = Path.home() / '.config/worktree-checker/config.json'
RETIRE_ROOT = Path.home() / '.local/state/worktree-preview-board/retired'
WEBHOOK_PORT = 7778
SCAN_INTERVAL = 60
MAX_DRAIN_PER_SCAN = 8
PR_FIELDS = 'number,state,headRefName,headRefOid,mergedAt,headRepository,headRepositoryOwner'
REPOSITORY_RE = re.compile(r'[\w.-]+/[\w.-]+')
REMOTE_RE = re.compile(
    r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
    r'([\w.-]+/[\w.-]+?)(?:\.git)?/?', re.IGNORECASE
)
PS_FORMAT = 'pid=,ppid=,uid=,lstart=,stat=,command='

_START_LOCK = threading.Lock()
_STARTED = None


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    uid: int
    birth: str
    command: str


def load_config():
    """Return normalized configured repositories; missing config means disabled."""
    try:
        data = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError('Cleanup config must be an object')
    cleanup = data.get('cleanup')
    if cleanup is None:
        return {}
    if not isinstance(cleanup, dict):
        raise ValueError('cleanup must be an object')
    repositories = cleanup.get('repositories')
    if repositories is None:
        return {}
    if not isinstance(repositories, dict):
        raise ValueError('cleanup.repositories must be an object')
    if not repositories:
        return {}
    mode = stat.S_IMODE(CONFIG.stat().st_mode)
    if mode & 0o077:
        raise ValueError(f'{CONFIG} must be private (mode 0600)')
    normalized = {}
    for repository, entry in repositories.items():
        if (not isinstance(repository, str)
                or not REPOSITORY_RE.fullmatch(repository)
                or repository.casefold() in normalized):
            raise ValueError('Invalid or duplicate repository name')
        if not isinstance(entry, dict):
            raise ValueError(f'{repository} must be an object')
        path = entry.get('path')
        secret = entry.get('webhook_secret', '')
        if not isinstance(path, str) or not os.path.isabs(path):
            raise ValueError(f'{repository}.path must be absolute')
        if not isinstance(secret, str):
            raise ValueError(f'{repository}.webhook_secret must be a string')
        normalized[repository.casefold()] = {
            'repository': repository,
            'path': Path(path).expanduser().resolve(),
            'webhook_secret': secret,
        }
    return normalized


def _run(args, timeout):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, 'LC_ALL': 'C'})


def _git(repo_root, *args, timeout=20):
    result = _run(['git', '-C', str(repo_root), *args], timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or '').strip() or f'git {args[0]} failed')
    return result.stdout.strip()


def repository_name(repo_root):
    remote = _git(repo_root, 'remote', 'get-url', 'origin', timeout=10)
    match = REMOTE_RE.fullmatch(remote)
    if not match:
        raise ValueError('origin must be a GitHub HTTPS or SSH repository URL')
    return match.group(1)


def worktrees(repo_root):
    """Parse Git's worktree listing, including the primary checkout."""
    result = _run(['git', '-C', str(repo_root), 'worktree', 'list', '--porcelain'], 20)
    if result.returncode:
        return []
    records = []
    for block in (result.stdout or '').split('\n\n'):
        record = {}
        for line in block.splitlines():
            key, _, value = line.partition(' ')
            if key == 'worktree':
                record['path'] = value
            elif key in ('HEAD', 'branch'):
                record[key] = value
            elif key == 'locked':
                record['locked'] = True
        if record.get('path'):
            records.append(record)
    return records


def matching_pr(tree, prs, repository):
    """Find the newest PR for this exact repository and head branch."""
    branch = tree.get('branch', '').removeprefix('refs/heads/')
    try:
        owner, name = repository.casefold().split('/', 1)
    except ValueError:
        return {}
    matches = [
        pr for pr in prs
        if branch
        and pr.get('headRefName') == branch
        and (pr.get('headRepository') or {}).get('name', '').casefold() == name
        and (pr.get('headRepositoryOwner') or {}).get('login', '').casefold() == owner
    ]
    def number(pr):
        try:
            return int(pr.get('number', 0))
        except (TypeError, ValueError):
            return 0

    return max(matches, key=number, default={})


def eligible(tree, prs, repo_root, repository):
    path = tree.get('path')
    if not path or Path(path).resolve() == Path(repo_root).resolve():
        return False
    pr = matching_pr(tree, prs, repository)
    return pr.get('state') == 'MERGED' and bool(pr.get('mergedAt'))


def _inside(cwd, path):
    if not cwd:
        return False
    try:
        cwd_path = Path(cwd).resolve()
        worktree_path = Path(path).resolve()
        return cwd_path == worktree_path or worktree_path in cwd_path.parents
    except OSError:
        return False


def _processes(pid=None):
    args = ['ps', '-p', str(pid), '-o', PS_FORMAT] if pid else ['ps', '-axo', PS_FORMAT]
    result = _run(args, 5)
    if result.returncode not in (0, 1):
        raise RuntimeError('Cannot inspect process ownership')
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 9)
        if len(fields) != 10:
            continue
        try:
            process_id, parent_id, uid = map(int, fields[:3])
        except ValueError:
            continue
        if uid != os.getuid() or fields[8].startswith('Z'):
            continue
        processes[process_id] = Process(
            process_id, parent_id, uid, ' '.join(fields[3:8]), fields[9]
        )
    return processes


def _same_process(saved, current):
    return bool(saved and current and saved.pid == current.pid and saved.uid == current.uid
                and saved.birth == current.birth and saved.command == current.command)


def _owner_pids(path):
    result = _run(['lsof', '-nP', '-a', '-u', str(os.getuid()), '-d', 'cwd', '-Fpn'], 15)
    if result.returncode not in (0, 1):
        raise RuntimeError('Cannot inspect worktree process ownership')
    owners, process_id = set(), None
    for line in result.stdout.splitlines():
        if line.startswith('p'):
            try:
                process_id = int(line[1:])
            except ValueError:
                process_id = None
        elif line.startswith('n') and process_id and _inside(line[1:], path):
            owners.add(process_id)
    return owners


def close_process_owners(path):
    """Stop same-user processes rooted in this worktree, safely by PID birth."""
    processes = _processes()
    roots = {
        pid for pid in _owner_pids(path)
        if pid in processes and pid not in (1, os.getpid())
    }
    while True:
        children = {p.pid for p in processes.values() if p.ppid in roots}
        if children <= roots:
            break
        roots |= children
    candidates = [processes[pid] for pid in roots]
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        for saved in candidates:
            current = _processes(saved.pid).get(saved.pid)
            if _same_process(saved, current):
                try:
                    os.kill(saved.pid, signal_number)
                except ProcessLookupError:
                    pass
        if signal_number == signal.SIGTERM:
            time.sleep(0.3)
    for _ in range(4):
        if not _owner_pids(path):
            return
        time.sleep(0.2)
    raise RuntimeError(f'Processes still own {path}')


def _gh_prs(repository):
    try:
        result = _run([
            'gh', 'pr', 'list', '--repo', repository, '--state', 'all', '--limit', '10000',
            '--json', PR_FIELDS,
        ], 45)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode:
        return []
    try:
        prs = json.loads(result.stdout)
    except (TypeError, ValueError):
        return []
    return prs if isinstance(prs, list) else []


def _retire_target():
    RETIRE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(RETIRE_ROOT, 0o700)
    except OSError:
        pass
    key = uuid.uuid4().hex
    return RETIRE_ROOT / key, RETIRE_ROOT / f'{key}.json'


def retire(tree, prs, repo_root, repository, preview_module):
    raw_path = tree.get('path')
    if not raw_path:
        return False
    path = Path(raw_path).expanduser()
    repo_root = Path(repo_root).resolve()
    if path.resolve() == repo_root:
        raise RuntimeError('The primary checkout cannot be retired')
    if path.is_symlink() or not (path / '.git').is_file():
        raise RuntimeError(f'Not a linked checkout: {path}')
    if not eligible(tree, prs, repo_root, repository):
        return False
    original = path.lstat()
    branch = tree.get('branch', '')
    if _git(path, 'symbolic-ref', 'HEAD', timeout=10) != branch:
        return False
    stopped = preview_module.stop_pair(str(path))
    if isinstance(stopped, dict) and not stopped.get('ok', True):
        raise RuntimeError(stopped.get('message') or 'Could not stop the preview')
    if _git(path, 'symbolic-ref', 'HEAD', timeout=10) != branch:
        return False
    close_process_owners(path)
    if _git(path, 'symbolic-ref', 'HEAD', timeout=10) != branch:
        return False
    current = path.lstat()
    if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
        return False
    if tree.get('locked'):
        _git(repo_root, 'worktree', 'unlock', str(path))
    pr = matching_pr(tree, prs, repository)
    target, manifest = _retire_target()
    if target.exists() or manifest.exists():
        raise RuntimeError(f'Pending retirement already exists for {path}')
    manifest.write_text(json.dumps({
        'original_path': str(path),
        'queued_path': str(target),
        'repository': repository,
        'pr': pr['number'],
        'branch': branch,
    }))
    os.chmod(manifest, 0o600)
    path.rename(target)
    _git(repo_root, 'worktree', 'prune', '--expire', 'now')
    return True


def drain_retired(limit=MAX_DRAIN_PER_SCAN):
    """Drain a bounded number of quarantined directories without blocking scans."""
    try:
        manifests = sorted(RETIRE_ROOT.glob('*.json'))[:limit]
    except OSError:
        return
    for manifest in manifests:
        try:
            record = json.loads(manifest.read_text())
            target = RETIRE_ROOT / manifest.stem
            original = Path(record['original_path'])
            if (record.get('queued_path') != str(target)
                    or not re.fullmatch(r'[0-9a-f]{32}', manifest.stem)
                    or target.is_symlink()):
                continue
            if not target.exists():
                if not original.exists():
                    manifest.unlink()
                continue
            shutil.rmtree(target)
            manifest.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            continue


def scan_once(preview_module):
    """Reload config and perform one safe scan of every enabled repository."""
    repositories = load_config()
    if not repositories:
        return
    for entry in repositories.values():
        repository = entry['repository']
        repo_root = entry['path']
        try:
            if repository_name(repo_root).casefold() != repository.casefold():
                continue
            records = worktrees(repo_root)
            if not records or Path(records[0]['path']).resolve() != repo_root:
                continue
            _git(repo_root, 'worktree', 'prune', '--expire', 'now')
            records = worktrees(repo_root)
            if not records or Path(records[0]['path']).resolve() != repo_root:
                continue
            prs = _gh_prs(repository)
            for tree in records[1:]:
                if tree.get('branch') and eligible(tree, prs, repo_root, repository):
                    try:
                        retire(tree, prs, repo_root, repository, preview_module)
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        traceback.print_exc()
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            traceback.print_exc()
    drain_retired()


def merged_pr_number(event, body, signature, repositories):
    """Validate a signed merged PR event and return its configured repo/number."""
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError('Expected an object')
    full_name = (payload.get('repository') or {}).get('full_name', '')
    entry = repositories.get(full_name.casefold())
    if not entry or not entry.get('webhook_secret'):
        return None
    expected = 'sha256=' + hmac.new(
        entry['webhook_secret'].encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature or ''):
        raise PermissionError('Invalid webhook signature')
    if event != 'pull_request' or payload.get('action') != 'closed':
        return None
    pr = payload.get('pull_request') or {}
    head_repo = ((pr.get('head') or {}).get('repo') or {}).get('full_name', '')
    if pr.get('merged') is not True or head_repo.casefold() != full_name.casefold():
        return None
    number = pr.get('number', payload.get('number'))
    if type(number) is not int or number < 1:
        return None
    return entry['repository'], number


def _handler(wake):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, body):
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            path = self.path.split('?', 1)[0]
            self._reply(200 if path == '/health' else 404,
                        'worktree checker\n' if path == '/health' else 'Not found\n')

        def do_POST(self):
            if self.path.split('?', 1)[0] != '/github':
                return self._reply(404, 'Not found\n')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 * 1024:
                    return self._reply(413, 'Invalid payload size\n')
                result = merged_pr_number(
                    self.headers.get('X-GitHub-Event', ''), self.rfile.read(length),
                    self.headers.get('X-Hub-Signature-256', ''), load_config(),
                )
            except PermissionError:
                return self._reply(403, 'Invalid signature\n')
            except (ValueError, TypeError, AttributeError):
                return self._reply(400, 'Invalid payload\n')
            if result:
                wake.set()
            self._reply(200, 'Accepted\n' if result else 'Ignored\n')

        def log_message(self, *_args):
            pass

    return Handler


def _worker(preview_module, wake):
    while True:
        try:
            scan_once(preview_module)
        except Exception:
            traceback.print_exc()
        wake.wait(SCAN_INTERVAL)
        wake.clear()


def start(preview_module):
    """Start one cleanup worker and one loopback-only webhook server."""
    global _STARTED
    with _START_LOCK:
        if _STARTED is not None:
            return _STARTED
        wake = threading.Event()
        worker = threading.Thread(
            target=_worker, args=(preview_module, wake), daemon=True,
            name='worktree-cleanup',
        )
        worker.start()
        try:
            server = HTTPServer(('127.0.0.1', WEBHOOK_PORT), _handler(wake))
        except OSError:
            server = None
            server_thread = None
        else:
            server_thread = threading.Thread(
                target=server.serve_forever, daemon=True, name='worktree-webhook',
            )
            server_thread.start()
        _STARTED = worker, server, server_thread
        return _STARTED
