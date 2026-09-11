# Merged PR cleanup

Worktree Checker can retire a linked worktree after its pull request merges.
This feature is off by default. An absent or empty config performs no cleanup.

Cleanup reads this file:

```text
~/.config/worktree-checker/config.json
```

Create it with one entry for each repository that you want to opt in:

```json
{
  "cleanup": {
    "repositories": {
      "owner/repository": {
        "path": "/absolute/path/to/the/primary/checkout",
        "webhook_secret": "optional-secret-for-this-repository"
      }
    }
  }
}
```

The path must be the primary checkout. The entry is matched to that checkout's
GitHub `origin` before any linked worktree is considered. The config contains a
webhook secret when webhooks are enabled, so keep it private:

```sh
mkdir -p ~/.config/worktree-checker
chmod 700 ~/.config/worktree-checker
chmod 600 ~/.config/worktree-checker/config.json
```

The app reloads the config on every scan and webhook event. It checks GitHub at
startup and every 60 seconds. A webhook only wakes that scan; Worktree Checker
still fetches GitHub state before it retires anything. The `gh` CLI must be
installed and authenticated for cleanup. If it is missing or cannot read the
repository, the local board continues to work, the worktree stays in place,
and `http://127.0.0.1:7777/health` reports the failed scan. This status page is
available even when the optional webhook listener is disabled.

The match requires the exact repository and exact PR head branch. The newest PR
for that branch wins, so do not reuse a branch for a new delivery after its old
PR merges. The primary checkout is never retired.

Before retirement, Worktree Checker stops the board's preview, stops
same-user processes whose current directory is the worktree (and their
descendants), and checks each process identity again before sending a signal.
Dirty files and active processes do not protect a merged linked worktree; enable
this feature only when that removal is acceptable. The directory is first moved
to the private quarantine at:

```text
~/.local/state/worktree-preview-board/retired/
```

The quarantine is drained in small bounded batches. A failed drain remains for a
later retry.

## Optional GitHub webhook

The webhook listener is loopback-only at `127.0.0.1:7778` when at least one
configured repository has a non-empty `webhook_secret`. With an absent or
empty config, Worktree Checker does not bind the port.

The endpoints are:

```text
POST /github
GET  /health
```

`GET /health` returns scan time, configured repository count, listener state,
and sanitized errors. It never exposes repository names or secrets. Configure
the repository's GitHub webhook to send signed
`pull_request` events to `/github`, with the same `webhook_secret` from the
local config and the `pull_request` event selected. GitHub webhook settings and
an optional reverse proxy or Tailscale route can expose that loopback endpoint;
the public route is deployment-specific and is not part of Worktree Checker.

Only signed `pull_request` events with action `closed`, `merged: true`, and a
same-repository head wake the worker. Fork PRs and all other events are ignored.
The periodic GitHub scan remains the recovery path for missed webhooks.

The cleanup service does not require an agent harness.
GitHub webhooks are the only event hook; the service does not run arbitrary
shell hooks. Quarantine removal runs in a separate low-priority worker, one
entry at a time, so a slow removal does not hold up repository scans.
