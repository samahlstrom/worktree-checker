import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / 'lib' / 'merged_pr_cleanup.py'
SPEC = importlib.util.spec_from_file_location('merged_pr_cleanup', MODULE_PATH)
cleanup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cleanup
SPEC.loader.exec_module(cleanup)


REPOSITORY = 'HealthTree/one'
BRANCH = 'refs/heads/fix/topic'
PRIMARY = Path('/repo')
WORKTREE = Path('/repo/worktree')


def merged_pr(number=42, *, repository='one', owner='HealthTree', branch='fix/topic', state='MERGED'):
    return {
        'number': number,
        'state': state,
        'mergedAt': '2026-09-10T00:00:00Z' if state == 'MERGED' else '',
        'headRefName': branch,
        'headRepository': {'name': repository},
        'headRepositoryOwner': {'login': owner},
    }


def tree(path=WORKTREE, branch=BRANCH):
    return {'path': path, 'branch': branch, 'HEAD': 'abc123'}


def event_body(*, repository=REPOSITORY, head_repository=REPOSITORY, number=42):
    return json.dumps({
        'action': 'closed',
        'repository': {'full_name': repository},
        'pull_request': {
            'number': number,
            'merged': True,
            'head': {'ref': 'fix/topic', 'repo': {'full_name': head_repository}},
        },
    }, separators=(',', ':')).encode()


def signature(body, secret='secret'):
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f'sha256={digest}'


class CleanupTests(unittest.TestCase):
    def test_missing_config_is_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(cleanup, 'CONFIG', Path(temporary) / 'missing.json'):
                self.assertEqual(cleanup.load_config(), {})

    def test_config_normalizes_repository_and_requires_absolute_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'config.json'
            config.write_text(json.dumps({'cleanup': {'repositories': {
                'HealthTree/one': {'path': '/repo', 'webhook_secret': 'secret'},
            }}}))
            config.chmod(0o600)
            with patch.object(cleanup, 'CONFIG', config):
                loaded = cleanup.load_config()
        self.assertEqual(loaded['healthtree/one']['repository'], REPOSITORY)
        self.assertEqual(loaded['healthtree/one']['path'], PRIMARY)

    def test_newest_exact_repo_and_branch_controls_eligibility(self):
        prs = [merged_pr(42), merged_pr(43, state='OPEN')]
        self.assertEqual(cleanup.matching_pr(tree(), prs, REPOSITORY)['number'], 43)
        self.assertFalse(cleanup.eligible(tree(), prs, PRIMARY, REPOSITORY))
        self.assertFalse(cleanup.eligible(
            tree(), [merged_pr(99, repository='fork', owner='someone')], PRIMARY, REPOSITORY
        ))
        self.assertFalse(cleanup.eligible(
            tree(path=PRIMARY), [merged_pr()], PRIMARY, REPOSITORY
        ))

    def test_webhook_requires_signed_same_repository_merged_event(self):
        repositories = {
            'healthtree/one': {
                'repository': REPOSITORY,
                'path': PRIMARY,
                'webhook_secret': 'secret',
            },
        }
        body = event_body(number=123)
        self.assertEqual(
            cleanup.merged_pr_number('pull_request', body, signature(body), repositories),
            (REPOSITORY, 123),
        )
        with self.assertRaises(PermissionError):
            cleanup.merged_pr_number('pull_request', body, 'sha256=bad', repositories)
        fork_body = event_body(head_repository='HealthTree/fork')
        self.assertIsNone(cleanup.merged_pr_number(
            'pull_request', fork_body, signature(fork_body), repositories
        ))

    def test_pid_birth_change_never_authorizes_signal(self):
        old = cleanup.Process(123, 1, 501, 'old-birth', 'node')
        reused = cleanup.Process(123, 1, 501, 'new-birth', 'node')
        self.assertFalse(cleanup._same_process(old, reused))
        self.assertTrue(cleanup._same_process(old, old))

    def test_github_failure_is_visible_and_does_not_look_empty(self):
        result = subprocess.CompletedProcess(['gh'], 1, '', 'authentication failed')
        with patch.object(cleanup, '_run', return_value=result):
            self.assertIsNone(cleanup._gh_prs(REPOSITORY))
        status = cleanup.health()
        self.assertFalse(status['ok'])
        self.assertTrue(status['errors'])
        self.assertNotIn('authentication failed', json.dumps(status))

    def test_disabled_config_does_not_bind_webhook_port(self):
        with patch.object(cleanup, 'HTTPServer') as server, \
                patch.object(cleanup, '_stop_server') as stop:
            cleanup._ensure_server({}, threading.Event())
        server.assert_not_called()
        stop.assert_called_once_with()

    def test_missing_quarantine_target_removes_stale_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'retired'
            root.mkdir()
            original = Path(temporary) / 'original'
            original.mkdir()
            key = 'a' * 32
            target = root / key
            manifest = root / f'{key}.json'
            manifest.write_text(json.dumps({
                'original_path': str(original),
                'queued_path': str(target),
            }))
            with patch.object(cleanup, 'RETIRE_ROOT', root), \
                    patch.object(cleanup, '_run') as run:
                cleanup.drain_retired()
            self.assertFalse(manifest.exists())
            run.assert_not_called()

    def test_retire_stops_pair_rechecks_branch_and_quarantines(self):
        branch = BRANCH
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / 'primary'
            worktree = root / 'worktree'
            retired = root / 'retired'
            primary.mkdir()
            worktree.mkdir()
            (worktree / '.git').write_text('gitdir: /tmp/worktree-admin\n')
            calls = []

            def git(path, *args, timeout=20):
                calls.append(('git', args))
                return branch if args[:2] == ('symbolic-ref', 'HEAD') else ''

            class Preview:
                def stop_pair(self, path):
                    calls.append(('stop', path))
                    return {'ok': True}

            candidate = tree(worktree)
            with patch.object(cleanup, 'RETIRE_ROOT', retired), \
                    patch.object(cleanup, '_git', side_effect=git), \
                    patch.object(cleanup, 'close_process_owners') as close:
                self.assertTrue(cleanup.retire(
                    candidate, [merged_pr()], primary, REPOSITORY, Preview()
                ))

            self.assertFalse(worktree.exists())
            self.assertEqual(len(list(retired.glob('*.json'))), 1)
            self.assertEqual(calls[1][0], 'stop')
            close.assert_called_once_with(worktree)

    def test_empty_scan_does_not_touch_github_or_retirement_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(cleanup, 'CONFIG', Path(temporary) / 'missing.json'), \
                    patch.object(cleanup, '_gh_prs') as gh, \
                    patch.object(cleanup, 'drain_retired') as drain:
                cleanup.scan_once(object())
        gh.assert_not_called()
        drain.assert_not_called()


if __name__ == '__main__':
    unittest.main()
