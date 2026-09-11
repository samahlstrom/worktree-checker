# Shared node_modules health check.
#
# Sourced by bin/worktree-setup (decides whether a package dir needs
# (re-)provisioning) and bin/worktree-doctor (detects drift after the fact).
# One definition so the two can never quietly disagree about what "healthy"
# means -- worktree-doctor used to carry its own separate `ls | wc -l`
# threshold check that drifted from provisioning's real `npm ls` check and
# stopped catching a real-but-empty node_modules (e.g. one containing only
# Vite's auto-created .vite cache dir).
#
# Usage: modules_are_healthy <path/to/node_modules>
#
# npm's --omit=optional makes a Linux esbuild tree look valid on macOS.  Check
# the optional host package and execute its real binary whenever esbuild is
# installed; projects that do not use esbuild keep the ordinary npm check.
host_esbuild_is_healthy() {
  local modules="$1" package_dir="${1%/node_modules}" system machine platform js binary actual
  [ -f "$modules/esbuild/package.json" ] || return 0
  system="$(uname -s | tr '[:upper:]' '[:lower:]')"
  machine="$(uname -m)"
  [ "$machine" != "aarch64" ] || machine="arm64"
  [ "$machine" != "x86_64" ] || machine="x64"
  platform="$system-$machine"
  binary="$modules/@esbuild/$platform/bin/esbuild"
  [ -x "$binary" ] || return 1
  js="$(cd "$package_dir" && node -p "require('./node_modules/esbuild/package.json').version" 2>/dev/null)" || return 1
  actual="$("$modules/.bin/esbuild" --version 2>/dev/null)" || return 1
  [ -n "$js" ] && [ "$js" = "$actual" ]
}

modules_are_healthy() {
  local modules="$1" package_dir="${1%/node_modules}"
  [ -d "$modules" ] && [ ! -L "$modules" ] && \
    ( cd "$package_dir" && npm ls --ignore-scripts --depth=0 >/dev/null 2>&1 ) && \
    host_esbuild_is_healthy "$modules"
}
