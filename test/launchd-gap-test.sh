#!/bin/bash
# test/launchd-gap-test.sh
#
# Isolates the ONE thing pure-bash tests can't verify: that the reworked
# LaunchAgent keys actually behave under launchd.
#
#   * AbandonProcessGroup=true  -> a script the runner backgrounds keeps running
#                                  (and logging) after the runner process exits,
#                                  instead of being killed by launchd.
#   * KeepAlive=false           -> launchd does NOT respawn the job the instant
#                                  it exits (no respawn storm); StartInterval
#                                  governs the cadence.
#
# It uses a TEMPORARY LaunchAgent with its own label and a self-contained
# launcher in a private sandbox under $HOME. It never touches
# com.joemoser.runner, the live repo, scripts.conf, runner.sh, or
# ~/Library/LaunchAgents. All changes are reversed at the end and on Ctrl-C/error.
#
# Run:  bash test/launchd-gap-test.sh     (takes ~40s; no sudo needed)

set -u

LABEL="com.joemoser.runner-selftest"
DOMAIN="gui/$(id -u)"
SB="$HOME/schedrunner-launchd-test"
PLIST="$SB/$LABEL.plist"
STARTINTERVAL=60     # production cadence; within a ~40s window only RunAtLoad fires
CHILD_SLEEP=20
WAIT=40

# ---------- (c) REVERSE — always runs, even on error / Ctrl-C ----------
teardown() {
  echo
  echo "== (c) teardown: reversing all test changes =="
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null \
    && echo "  unloaded $LABEL" \
    || echo "  $LABEL not loaded (nothing to unload)"
  pkill -f "$SB/launcher.sh" 2>/dev/null
  rm -rf "$SB" && echo "  removed sandbox $SB"
  echo "  com.joemoser.runner and the live repo were never touched."
}
trap teardown EXIT INT TERM

# ---------- (a) SETUP ----------
echo "== (a) setup =="
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null   # clear any leftover from an aborted run
rm -rf "$SB"; mkdir -p "$SB"

# Minimal stand-in for runner.sh: record a tick, background a child that should
# outlive us, then exit immediately — exactly the runner's launch pattern.
cat > "$SB/launcher.sh" <<EOF
#!/bin/bash
echo "tick \$(date +%s)" >> "$SB/ticks.log"
(
  sleep $CHILD_SLEEP
  echo "child survived launcher exit, finished at \$(date +%s)" >> "$SB/child.log"
) >> "$SB/child.log" 2>&1 &
disown
exit 0
EOF
chmod +x "$SB/launcher.sh"
: > "$SB/ticks.log"; : > "$SB/child.log"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$SB/launcher.sh</string></array>
  <key>StartInterval</key><integer>$STARTINTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$SB/launcher.out</string>
  <key>StandardErrorPath</key><string>$SB/launcher.err</string>
</dict></plist>
EOF
chmod 644 "$PLIST"

echo "  sandbox: $SB"
echo "  loading temporary LaunchAgent: $LABEL"
if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
  echo "  ERROR: 'launchctl bootstrap' failed. If your macOS is older, try the legacy form:"
  echo "         launchctl load -w \"$PLIST\"   (and 'launchctl unload' to reverse)"
  exit 2
fi
echo "  loaded. waiting ${WAIT}s for the backgrounded run to outlive its launcher..."
sleep "$WAIT"

# ---------- (b) REPORT ----------
echo
echo "== (b) results =="
ticks=$(grep -c '^tick ' "$SB/ticks.log" 2>/dev/null);            ticks=${ticks:-0}
survived=$(grep -c 'child survived' "$SB/child.log" 2>/dev/null); survived=${survived:-0}
pass=0; fail=0

if [ "$survived" -ge 1 ]; then
  echo "  PASS  AbandonProcessGroup: the backgrounded script kept running and logged"
  echo "        AFTER the launcher process had already exited."
  pass=$((pass + 1))
else
  echo "  FAIL  AbandonProcessGroup: the backgrounded script did NOT survive the launcher"
  echo "        exit — launchd killed it. The detached-runner design needs this key."
  fail=$((fail + 1))
fi

if [ "$ticks" -eq 1 ]; then
  echo "  PASS  KeepAlive=false: launcher ran once (RunAtLoad) and was not respawned"
  echo "        within ${WAIT}s — no respawn storm (next tick would be at ${STARTINTERVAL}s)."
  pass=$((pass + 1))
else
  echo "  WARN  KeepAlive: expected exactly 1 tick in ${WAIT}s (StartInterval=${STARTINTERVAL}s),"
  echo "        but saw ${ticks}. More than 1 suggests launchd is respawning — investigate."
  fail=$((fail + 1))
fi

echo
echo "  ticks.log:"; sed 's/^/    /' "$SB/ticks.log" 2>/dev/null
echo "  child.log:"; sed 's/^/    /' "$SB/child.log" 2>/dev/null
echo
echo "  SUMMARY: $pass passed, $fail failed/warned"
# teardown runs automatically next, via the EXIT trap
