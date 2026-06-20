#!/bin/bash
# test_runner.sh — runner.sh cadence evaluation, validation, and the per-script
# lock that prevents a slow script from overlapping itself.
#
# We run the REAL runner.sh from a sandbox dir (so its SCRIPT_DIR-derived
# scripts.conf/.last_run_times/log live in the sandbox) and drive it with tiny
# sentinel scripts. Scripts are launched DETACHED, so we poll for their effect.
source "$(dirname "$0")/lib.sh"

LOCK_BASE="/tmp/schedrunner-locks"   # hardcoded in runner.sh

new_sandbox() {
  local d; d="$(make_tmpdir)"
  cp "$REPO_ROOT/runner.sh" "$d/runner.sh"
  mkdir -p "$d/log"
  printf '%s' "$d"
}

# make_sentinel <script_path> <sentinel_file_it_creates>
make_sentinel() {
  cat > "$1" <<EOF
#!/bin/bash
echo ran > "$2"
EOF
  chmod +x "$1"
}

lock_dir_for() { printf '%s/%s' "$LOCK_BASE" "$(echo "$1" | tr -c 'A-Za-z0-9._-' '_')"; }

# --- happy path: an interval that is due runs -------------------------------
d="$(new_sandbox)"; s="$d/job.sh"; sent="$d/ran.flag"
make_sentinel "$s" "$sent"
printf 'interval|1|%s\n' "$s" > "$d/scripts.conf"
bash "$d/runner.sh" >/dev/null 2>&1
poll_until 5 test -f "$sent"
assert_file "$sent" "interval due: script executed"
assert_contains "$(cat "$d/.last_run_times" 2>/dev/null)" "$s" "interval due: last-run timestamp recorded"

# --- behavior: a .sh script with no execute bit still runs ------------------
# Dropbox does not sync execute bits across devices, so runner.sh invokes .sh
# entries via `bash` rather than relying on +x.
d="$(new_sandbox)"; s="$d/noexec.sh"; sent="$d/ran.flag"
make_sentinel "$s" "$sent"; chmod -x "$s"
printf 'interval|1|%s\n' "$s" > "$d/scripts.conf"
bash "$d/runner.sh" >/dev/null 2>&1
poll_until 5 test -f "$sent"
assert_file "$sent" "non-executable .sh: still runs (invoked via bash, not +x)"

# --- edge: an interval that is NOT yet due does not run ---------------------
d="$(new_sandbox)"; s="$d/job.sh"; sent="$d/ran.flag"
make_sentinel "$s" "$sent"
printf 'interval|10|%s\n' "$s" > "$d/scripts.conf"
printf '%s|%s\n' "$s" "$(date +%s)" > "$d/.last_run_times"   # ran just now
bash "$d/runner.sh" >/dev/null 2>&1
assert_no_file "$sent" "interval not due: script did NOT run"
assert_contains "$(cat "$d/.last_run_times")" "$s" "interval not due: existing timestamp preserved"

# --- edge: comments, blank, and malformed lines are skipped without error ---
d="$(new_sandbox)"
printf '# a comment\n\ngarbage_no_delimiters\n' > "$d/scripts.conf"
bash "$d/runner.sh" >/dev/null 2>&1; rc=$?
assert_status 0 "$rc" "comment/blank/malformed: runner exits 0"

# --- error: interval value is not a number ----------------------------------
d="$(new_sandbox)"; s="$d/job.sh"; sent="$d/ran.flag"; make_sentinel "$s" "$sent"
printf 'interval|abc|%s\n' "$s" > "$d/scripts.conf"
out="$(bash "$d/runner.sh" 2>&1)"
assert_contains "$out" "is not a number" "bad interval value: warns"
assert_no_file "$sent" "bad interval value: does not run the script"

# --- error: unknown cadence type --------------------------------------------
d="$(new_sandbox)"
printf 'weekly|1|/abs/x.sh\n' > "$d/scripts.conf"
out="$(bash "$d/runner.sh" 2>&1)"
assert_contains "$out" "Unknown cadence type" "unknown cadence: warns"

# --- error: daily value not HH:MM -------------------------------------------
d="$(new_sandbox)"
printf 'daily|24:00|/abs/x.sh\n' > "$d/scripts.conf"
out="$(bash "$d/runner.sh" 2>&1)"
assert_contains "$out" "is not in HH:MM format" "bad daily time: warns"

# --- edge: daily whose time is not the current minute does not run ----------
d="$(new_sandbox)"; s="$d/job.sh"; sent="$d/ran.flag"; make_sentinel "$s" "$sent"
other="00:00"; [[ "$(date +%H:%M)" == "00:00" ]] && other="23:59"
printf 'daily|%s|%s\n' "$other" "$s" > "$d/scripts.conf"
bash "$d/runner.sh" >/dev/null 2>&1
assert_no_file "$sent" "daily non-matching time: script did NOT run"

# --- behavior: per-script lock prevents overlap -----------------------------
d="$(new_sandbox)"; s="$d/job.sh"; sent="$d/ran.flag"; make_sentinel "$s" "$sent"
printf 'interval|1|%s\n' "$s" > "$d/scripts.conf"
ld="$(lock_dir_for "$s")"; mkdir -p "$ld"; echo $$ > "$ld/pid"   # $$ is alive
bash "$d/runner.sh" >/dev/null 2>&1
assert_no_file "$sent" "lock held by live pid: script skipped (no overlap)"
assert_contains "$(cat "$d/log/job.sh.log" 2>/dev/null)" "Skipped (still running)" "lock held: logs the skip"
rm -rf "$ld"

# --- behavior: a stale lock (dead pid) is reclaimed and the script runs -----
d="$(new_sandbox)"; s="$d/job.sh"; sent="$d/ran.flag"; make_sentinel "$s" "$sent"
printf 'interval|1|%s\n' "$s" > "$d/scripts.conf"
ld="$(lock_dir_for "$s")"; mkdir -p "$ld"; echo 999999 > "$ld/pid"   # almost certainly dead
bash "$d/runner.sh" >/dev/null 2>&1
poll_until 5 test -f "$sent"
assert_file "$sent" "stale lock (dead pid): reclaimed and script ran"
rm -rf "$ld"

finish
