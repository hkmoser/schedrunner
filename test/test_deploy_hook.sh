#!/bin/bash
# test_deploy_hook.sh — schedrunner's own .auto-deploy post-pull hook. It must
# reload the LaunchAgent only when the running plist differs from the repo's, so
# a plist change (e.g. AbandonProcessGroup) actually takes effect after a pull.
#
# The reload itself (loader/install.sh -> launchctl) is macOS-only and is mocked
# via SCHEDRUNNER_RELOAD_CMD; everything else (the cmp decision) runs for real.
source "$(dirname "$0")/lib.sh"

# Run the REAL .auto-deploy from a sandbox laid out like the repo root, with the
# reload action replaced by a recorder. Sets globals: HOOK_OUT, HOOK_RC, and
# RELOAD_FLAG (the file the recorder touches iff a reload happened).
#   run_hook <installed_plist_contents | MISSING> <repo_plist_contents>
run_hook() {
  local installed="$1" repo_plist="$2" d installed_path
  d="$(make_tmpdir)"
  cp "$REPO_ROOT/.auto-deploy" "$d/.auto-deploy"
  mkdir -p "$d/loader"
  printf '%s' "$repo_plist" > "$d/loader/com.joemoser.runner.plist"

  installed_path="$d/installed.plist"
  if [[ "$installed" == MISSING ]]; then
    installed_path="$d/does-not-exist.plist"
  else
    printf '%s' "$installed" > "$installed_path"
  fi

  RELOAD_FLAG="$d/reloaded.flag"
  HOOK_OUT="$( cd "$d" \
    && SCHEDRUNNER_INSTALLED_PLIST="$installed_path" \
       SCHEDRUNNER_RELOAD_CMD="echo did-reload > '$RELOAD_FLAG'" \
       bash .auto-deploy )"
  HOOK_RC=$?
}

# --- unchanged plist -> no reload -------------------------------------------
run_hook "PLIST-V1" "PLIST-V1"
assert_status 0 "$HOOK_RC" "unchanged plist: hook exits 0"
assert_contains "$HOOK_OUT" "unchanged — no reload needed" "unchanged plist: reports no reload"
assert_no_file "$RELOAD_FLAG" "unchanged plist: reload NOT invoked"

# --- changed plist -> reload ------------------------------------------------
run_hook "PLIST-V1" "PLIST-V2-with-AbandonProcessGroup"
assert_status 0 "$HOOK_RC" "changed plist: hook exits 0"
assert_contains "$HOOK_OUT" "reloading" "changed plist: reports reloading"
assert_file "$RELOAD_FLAG" "changed plist: reload invoked"

# --- plist not installed yet -> reload --------------------------------------
run_hook "MISSING" "PLIST-V1"
assert_status 0 "$HOOK_RC" "not installed: hook exits 0"
assert_file "$RELOAD_FLAG" "not installed: reload invoked"

# --- a failing reload surfaces as a non-zero hook exit (auto-deploy logs it) -
d="$(make_tmpdir)"; cp "$REPO_ROOT/.auto-deploy" "$d/.auto-deploy"
mkdir -p "$d/loader"; printf 'A' > "$d/loader/com.joemoser.runner.plist"
rc=0
( cd "$d" \
  && SCHEDRUNNER_INSTALLED_PLIST="$d/none.plist" \
     SCHEDRUNNER_RELOAD_CMD="exit 7" \
     bash .auto-deploy ) >/dev/null 2>&1 || rc=$?
assert_status 7 "$rc" "failing reload: hook propagates the failure"

finish
