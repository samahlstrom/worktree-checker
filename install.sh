#!/bin/sh
set -eu
case "$(uname -s)" in Darwin) ;; *) echo 'This app download is for macOS. The source also runs on POSIX systems with Python.' >&2; exit 1;; esac
case "$(uname -m)" in arm64) arch=arm64;; x86_64) arch=x64;; *) echo 'Unsupported Mac architecture.' >&2; exit 1;; esac
worktree_download=$(mktemp -d)
trap 'rm -rf "$worktree_download"' EXIT HUP INT TERM
asset="worktree-checker-macos-$arch.zip"
base='https://github.com/samahlstrom/worktree-checker/releases/latest/download'
curl -fL --retry 3 "$base/$asset" -o "$worktree_download/$asset"
curl -fL --retry 3 "$base/$asset.sha256" -o "$worktree_download/$asset.sha256"
(cd "$worktree_download" && shasum -a 256 -c "$asset.sha256")
ditto -x -k "$worktree_download/$asset" "$worktree_download"
mkdir -p "$HOME/Applications"
launchctl bootout "gui/$(id -u)/local.worktree-checker" >/dev/null 2>&1 || true
if [ -e "$HOME/Applications/Worktree Checker.app" ]; then
  mv "$HOME/Applications/Worktree Checker.app" "$worktree_download/previous.app"
fi
if ! mv "$worktree_download/Worktree Checker.app" "$HOME/Applications/Worktree Checker.app"; then
  if [ -e "$worktree_download/previous.app" ]; then
    mv "$worktree_download/previous.app" "$HOME/Applications/Worktree Checker.app"
  fi
  exit 1
fi
open "$HOME/Applications/Worktree Checker.app"
echo 'Worktree Checker is installed. Your board is opening.'
