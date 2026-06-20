#!/bin/bash
# test_auto_deploy.sh — auto-deploy.sh fetch/reset/post-pull-hook behavior and
# the single-instance lock. Uses REAL git: a bare "remote" plus a working clone
# under a sandboxed $HOME/Dropbox/Source (auto-deploy.sh derives SOURCE_DIR from
# $HOME). No gh is involved in this version of the script.
source "$(dirname "$0")/lib.sh"

LOCK="/tmp/auto-deploy-poll.lock"   # hardcoded in auto-deploy.sh
rm -f "$LOCK"

# setup_repo <home> <name> <auto_deploy_file_contents>
setup_repo() {
  local home="$1" name="$2" hook="$3"
  local remote="$home/remotes/$name.git" seed="$home/seed-$name" src="$home/Dropbox/Source/$name"
  mkdir -p "$home/remotes" "$home/Dropbox/Source"
  git init -q --bare "$remote"
  git clone -q "$remote" "$seed"
  ( cd "$seed"
    echo v1 > file.txt
    printf '%s' "$hook" > .auto-deploy
    git add -A; git commit -qm init; git push -q origin HEAD:main )
  rm -rf "$seed"
  git clone -q "$remote" "$src"
  git -C "$src" remote set-head origin main 2>/dev/null
}

# advance_remote <home> <name> — push a new commit so the remote is ahead
advance_remote() {
  local home="$1" name="$2"
  local remote="$home/remotes/$name.git" seed="$home/adv-$name"
  git clone -q "$remote" "$seed"
  ( cd "$seed"
    echo advanced > advanced.txt
    git add -A; git commit -qm advance; git push -q origin HEAD:main )
  rm -rf "$seed"
}

run_autodeploy() { HOME="$1" bash "$REPO_ROOT/auto-deploy.sh" 2>&1; }

# --- happy path: remote advanced, empty .auto-deploy -> reset only ----------
h="$(make_tmpdir)"; setup_repo "$h" demo ""
advance_remote "$h" demo
out="$(run_autodeploy "$h")"
assert_file "$h/Dropbox/Source/demo/advanced.txt" "advance + empty hook: working tree reset to remote"
assert_contains "$out" "reset OK (no post-pull script)" "advance + empty hook: logs reset-only"
assert_contains "$out" "1 repo(s) updated" "advance + empty hook: counts the update"

# --- happy path: remote advanced, non-empty hook runs after reset -----------
h="$(make_tmpdir)"; setup_repo "$h" demo "echo ran > $h/hook.flag"
advance_remote "$h" demo
out="$(run_autodeploy "$h")"
assert_file "$h/hook.flag" "advance + hook: post-pull hook executed"
assert_contains "$out" "running .auto-deploy" "advance + hook: logs hook run"
assert_contains "$out" "deploy OK" "advance + hook: logs deploy OK"

# --- edge: nothing to do when already up to date ----------------------------
h="$(make_tmpdir)"; setup_repo "$h" demo ""
out="$(run_autodeploy "$h")"
assert_contains "$out" "up to date" "no advance: logs up to date"
assert_not_contains "$out" "repo(s) updated" "no advance: nothing reported as updated"
assert_no_file "$h/Dropbox/Source/demo/advanced.txt" "no advance: working tree untouched"

# --- error: a failing post-pull hook is reported, not swallowed -------------
h="$(make_tmpdir)"; setup_repo "$h" demo "exit 1"
advance_remote "$h" demo
out="$(run_autodeploy "$h")"
assert_contains "$out" ".auto-deploy FAILED" "failing hook: reported as FAILED"

# --- behavior: single-instance lock skips when another run is live ----------
h="$(make_tmpdir)"; setup_repo "$h" demo ""
advance_remote "$h" demo
echo $$ > "$LOCK"                       # $$ is a live pid
out="$(run_autodeploy "$h")"
assert_contains "$out" "already running" "live lock: second run skips"
assert_no_file "$h/Dropbox/Source/demo/advanced.txt" "live lock: no deploy happened"
rm -f "$LOCK"

finish
