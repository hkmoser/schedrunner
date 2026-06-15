#!/bin/bash
# test/test-runner.sh
#
# Self-contained test for the non-blocking runner. It does NOT touch your live
# schedrunner setup: it copies runner.sh into a private temp sandbox with its
# own scripts.conf, log/, .last_run_times, and lock directory (the lock path is
# rewritten into the sandbox), runs ticks there, and asserts the behavior.
#
# Safe to run on the Mac at any time, even while the real scheduler is running.
# Nothing here registers itself with launchd or with scripts.conf.
#
# Run:   bash test/test-runner.sh
# Exit:  0 if all checks pass, 1 otherwise.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER_SRC="$REPO_DIR/runner.sh"

PASS=0; FAIL=0
ok()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

[ -f "$RUNNER_SRC" ] || { echo "cannot find runner.sh at $RUNNER_SRC"; exit 2; }

SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/schedrunner-test.XXXXXX")"
LOCKS="$SANDBOX/locks"
cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT
echo "Sandbox: $SANDBOX"
echo

# Copy runner.sh, redirecting its lock base into the sandbox so a real run is
# never touched. Everything else (scripts.conf, log/, .last_run_times) is read
# relative to the copy's own location, which is the sandbox.
sed "s#^LOCK_BASE=.*#LOCK_BASE=\"$LOCKS\"#" "$RUNNER_SRC" > "$SANDBOX/runner.sh"
mkdir -p "$SANDBOX/log"
: > "$SANDBOX/.last_run_times"

cat > "$SANDBOX/fast.sh" <<'EOF'
#!/bin/bash
echo "fast output line"
EOF
cat > "$SANDBOX/slow.sh" <<'EOF'
#!/bin/bash
sleep 4
echo "slow finished"
EOF
chmod +x "$SANDBOX/fast.sh" "$SANDBOX/slow.sh"

run_tick() { ( cd "$SANDBOX" && bash runner.sh ); }
reset()    { rm -rf "$LOCKS"/* 2>/dev/null; rm -f "$SANDBOX"/log/* 2>/dev/null; : > "$SANDBOX/.last_run_times"; }

# ---------------------------------------------------------------------------
echo "[1] non-blocking + parallel + logging"
cat > "$SANDBOX/scripts.conf" <<EOF
interval|1|$SANDBOX/fast.sh
interval|1|$SANDBOX/slow.sh
EOF
start=$(date +%s); run_tick; end=$(date +%s); elapsed=$((end - start))
if [ "$elapsed" -le 2 ]; then ok "runner returned in ${elapsed}s while slow.sh sleeps 4s (non-blocking)"
else bad "runner took ${elapsed}s — it blocked on slow.sh"; fi
sleep 1
grep -q "fast output line" "$SANDBOX/log/fast.sh.log" 2>/dev/null \
  && ok "fast.sh executed and output captured" || bad "fast.sh did not run/log"
grep -q "Running .*slow.sh" "$SANDBOX/log/slow.sh.log" 2>/dev/null \
  && ok "slow.sh launched in the same tick (parallel)" || bad "slow.sh was not launched"

echo "    ...waiting for slow.sh to finish"
sleep 5
grep -q "slow finished" "$SANDBOX/log/slow.sh.log" 2>/dev/null \
  && ok "slow.sh completed and logged after the runner had already exited" \
  || bad "slow.sh output was not captured to its log"
[ -z "$(ls -A "$LOCKS" 2>/dev/null)" ] \
  && ok "lock released automatically when slow.sh finished" || bad "lock was not released"

# ---------------------------------------------------------------------------
echo "[2] overlap prevention (a long script does not pile up)"
reset
echo "interval|0|$SANDBOX/slow.sh" > "$SANDBOX/scripts.conf"   # interval 0 = always due
run_tick; run_tick; run_tick
r=$(grep -c "Running" "$SANDBOX/log/slow.sh.log" 2>/dev/null)
s=$(grep -c "Skipped" "$SANDBOX/log/slow.sh.log" 2>/dev/null)
if [ "${r:-0}" -eq 1 ] && [ "${s:-0}" -eq 2 ]; then ok "3 ticks while running -> 1 run, 2 skipped"
else bad "expected 1 run + 2 skipped, got run=$r skipped=$s"; fi
echo "    ...waiting for slow.sh to finish"
sleep 5

# ---------------------------------------------------------------------------
echo "[3] stale lock (dead pid) is reclaimed"
reset
echo "interval|0|$SANDBOX/fast.sh" > "$SANDBOX/scripts.conf"
key="$(echo "$SANDBOX/fast.sh" | tr -c 'A-Za-z0-9._-' '_')"
mkdir -p "$LOCKS/$key"; echo 999999 > "$LOCKS/$key/pid"   # pid 999999 is not alive
run_tick; sleep 1
grep -q "fast output line" "$SANDBOX/log/fast.sh.log" 2>/dev/null \
  && ok "stale lock reclaimed and script ran" || bad "stale lock was not reclaimed (script skipped)"

# ---------------------------------------------------------------------------
echo "[4] fail-open when the lock directory is unusable"
reset
rm -rf "$LOCKS"; : > "$LOCKS"   # put a FILE where the lock dir should be
echo "interval|0|$SANDBOX/fast.sh" > "$SANDBOX/scripts.conf"
run_tick 2>/dev/null; sleep 1
grep -q "fast output line" "$SANDBOX/log/fast.sh.log" 2>/dev/null \
  && ok "ran anyway despite a broken lock dir (locking fails open)" \
  || bad "script was skipped when locking was unavailable"

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
