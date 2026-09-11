# Worktree Checker

A local board for your Git worktrees. Open it, pick a branch, and start its preview.

Worktree Checker finds local repositories itself. The board follows Git as worktrees
appear and disappear. It does not need an agent harness, an account, or a project
registry. GitHub PR information is optional and never blocks the local list.

## Download

On a Mac, paste this once into Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/samahlstrom/worktree-checker/main/install.sh | sh
```

It downloads the app, installs it in your Applications folder, and opens the board.
There is no repository setup step.

The first background scan may take some time. A repository in an unusual folder is
found on the next scan, about every 60 seconds; worktrees of known repositories update
in about 500 milliseconds.

Or download the macOS ZIP for your Mac from [Releases](https://github.com/samahlstrom/worktree-checker/releases).
Unzip, move **Worktree Checker.app** to Applications, and open it. The board opens at
<http://127.0.0.1:7777> and starts at login thereafter.

The app includes Python for the board and Node/npm for JavaScript projects. Other
project stacks need their own runtime installed. macOS releases are currently unsigned; macOS
may require approval in **System Settings → Privacy & Security → Open Anyway**.

## Previews

- Start uses the repository's own development command when one is present.
- Node projects may declare additional local services with `<name>:dev` package scripts. A matching
  `<name>:emulator` takes precedence for seeded local development. The board passes its allocated
  port as both `PORT` and `<NAME>_PORT`, then gives each service independent controls.
- Start calls **Worktree Doctor** to repair missing packages, broken generated-file links, and missing ignored local settings. Compatible existing installs can
  be cloned on APFS without sharing writable dependency files.
- Existing local environment files supply configuration; nothing is uploaded.
- Start and Stop are serialized. A failed start cleans up the new service.
- Stop waits for the managed processes to exit. Repeating Stop is safe.
- The board keeps the original per-worktree controls, search, PR links, and live-edit behavior.

Single-service projects keep the compact worktree controls. Project services still need their
actual credentials and required external services.
The board does not create production secrets or infer a server for a library that
has no run command.

You can also run Doctor directly:

```sh
worktree-doctor /path/to/worktree
worktree-doctor /path/to/worktree --fix
```

Doctor and Start use the same repair code. Doctor does not create a second workspace
or start another development server.

## Optional merged-PR cleanup

Cleanup is off unless you enable a repository in the local config. It checks GitHub
at startup and periodically, and can receive signed GitHub `pull_request` webhooks
for immediate cleanup. See [webhook setup](docs/webhooks.md).

## Run from source

Python 3.12+, Git, and the runtime used by your project are needed when running from source.

```sh
git clone https://github.com/samahlstrom/worktree-checker.git
cd worktree-checker
python3 app.py
```

`python3 app.py --serve` runs the board without opening a browser.
Automated tests run in GitHub Actions. Release builds use
[PyInstaller](https://pyinstaller.org/en/stable/runtime-information.html); bundled Node
comes from the [official Node.js release](https://nodejs.org/en/download).

## Local data

Server state and logs: `~/.local/state/worktree-preview-board/`.
Project setup logs and generated preview files: `<worktree>/.preview/`.
The app listens on loopback. Expose only the webhook route if you configure a public receiver.
