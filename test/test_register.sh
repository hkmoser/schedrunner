#!/bin/bash
# test_register.sh — register.sh validates cadence/path and appends to
# scripts.conf without duplicating. Pure logic: no network, no I/O beyond the
# config file it owns. We run the REAL register.sh from a temp dir so its
# SCRIPT_DIR (and thus scripts.conf) is the sandbox.
source "$(dirname "$0")/lib.sh"

# fresh sandbox with a copy of register.sh; returns the dir
new_sandbox() {
  local d; d="$(make_tmpdir)"
  cp "$REPO_ROOT/register.sh" "$d/register.sh"
  printf '%s' "$d"
}

# --- happy path: interval ---------------------------------------------------
d="$(new_sandbox)"
out="$(bash "$d/register.sh" interval 5 /abs/heartbeat.sh 2>&1)"; rc=$?
assert_status 0 "$rc" "interval valid: exit 0"
assert_contains "$out" "Registered: interval|5|/abs/heartbeat.sh" "interval valid: confirms record"
assert_contains "$(cat "$d/scripts.conf")" "interval|5|/abs/heartbeat.sh" "interval valid: appended to scripts.conf"

# --- happy path: daily ------------------------------------------------------
d="$(new_sandbox)"
bash "$d/register.sh" daily 08:02 /abs/report.sh >/dev/null 2>&1
assert_contains "$(cat "$d/scripts.conf")" "daily|08:02|/abs/report.sh" "daily valid: appended"

# --- happy path: startup normalizes value to 'ignored' ----------------------
d="$(new_sandbox)"
bash "$d/register.sh" startup - /abs/on_boot.sh >/dev/null 2>&1
assert_contains "$(cat "$d/scripts.conf")" "startup|ignored|/abs/on_boot.sh" "startup: value normalized to 'ignored'"

# --- happy path: interpreter + script joined with a space -------------------
d="$(new_sandbox)"
bash "$d/register.sh" interval 10 /abs/.venv/bin/python /abs/job.py >/dev/null 2>&1
assert_contains "$(cat "$d/scripts.conf")" "interval|10|/abs/.venv/bin/python /abs/job.py" "multi-token: interpreter+script joined"

# --- edge: duplicate is not appended twice ----------------------------------
d="$(new_sandbox)"
bash "$d/register.sh" interval 5 /abs/heartbeat.sh >/dev/null 2>&1
out="$(bash "$d/register.sh" interval 5 /abs/heartbeat.sh 2>&1)"; rc=$?
assert_status 0 "$rc" "duplicate: exit 0"
assert_contains "$out" "Already registered" "duplicate: reports already registered"
count="$(grep -c -F 'interval|5|/abs/heartbeat.sh' "$d/scripts.conf")"
assert_eq 1 "$count" "duplicate: record present exactly once"

# --- error: non-numeric interval -------------------------------------------
d="$(new_sandbox)"
out="$(bash "$d/register.sh" interval abc /abs/x.sh 2>&1)"; rc=$?
assert_status 1 "$rc" "bad interval: exit 1"
assert_contains "$out" "interval value must be an integer" "bad interval: explains why"
assert_no_file "$d/scripts.conf" "bad interval: nothing written"

# --- error: bad daily time --------------------------------------------------
d="$(new_sandbox)"
out="$(bash "$d/register.sh" daily 25:99 /abs/x.sh 2>&1)"; rc=$?
assert_status 1 "$rc" "bad daily time: exit 1"
assert_contains "$out" "daily value must be HH:MM" "bad daily time: explains why"

# --- error: unknown cadence type --------------------------------------------
d="$(new_sandbox)"
out="$(bash "$d/register.sh" weekly 1 /abs/x.sh 2>&1)"; rc=$?
assert_status 1 "$rc" "unknown cadence: exit 1"
assert_contains "$out" "unknown cadence type" "unknown cadence: explains why"

# --- error: relative script path --------------------------------------------
d="$(new_sandbox)"
out="$(bash "$d/register.sh" interval 5 relative/path.sh 2>&1)"; rc=$?
assert_status 1 "$rc" "relative path: exit 1"
assert_contains "$out" "must be absolute" "relative path: explains why"

# --- error: too few arguments -----------------------------------------------
d="$(new_sandbox)"
out="$(bash "$d/register.sh" interval 5 2>&1)"; rc=$?
assert_status 1 "$rc" "too few args: exit 1"
assert_contains "$out" "usage" "too few args: prints usage"

finish
