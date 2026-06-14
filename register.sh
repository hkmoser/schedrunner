#!/bin/bash
# register.sh — register a script with schedrunner.
#
# Appends a validated record to scripts.conf (next to this script) so runner.sh
# will run a script on a schedule. Run from anywhere. Commit scripts.conf
# afterward; the next minute tick picks it up — no reload needed.
#
# Usage:
#   ./register.sh interval <minutes> <absolute_path> [args...]
#   ./register.sh daily    <HH:MM>   <absolute_path> [args...]
#   ./register.sh startup  -         <absolute_path> [args...]
#
# Examples:
#   ./register.sh interval 5 /Users/joemoser/Dropbox/Source/myrepo/heartbeat.sh
#   ./register.sh daily 08:02 /Users/joemoser/Dropbox/Source/myrepo/report.sh
#   ./register.sh interval 10 \
#       /Users/joemoser/Dropbox/Source/myrepo/.venv/bin/python \
#       /Users/joemoser/Dropbox/Source/myrepo/job.py
#
# For auto-deploy (pull + redeploy on push) you don't need this helper — just
# add a .auto-deploy file to the target repo's root. See CLAUDE.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/scripts.conf"

die() { echo "register.sh: $*" >&2; exit 1; }

[[ $# -ge 3 ]] || die "usage: register.sh <interval|daily|startup> <value> <absolute_path> [args...]"

cadence_type="$1"
cadence_value="$2"
shift 2

# The first remaining token is the executable/interpreter; it must be absolute,
# because schedrunner does not cd into the repo before running.
[[ "$1" = /* ]] || die "script path must be absolute: $1"
script_path="$*"

case "$cadence_type" in
  interval)
    [[ "$cadence_value" =~ ^[0-9]+$ ]] \
      || die "interval value must be an integer number of minutes: $cadence_value"
    ;;
  daily)
    [[ "$cadence_value" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] \
      || die "daily value must be HH:MM (24h): $cadence_value"
    ;;
  startup)
    cadence_value="ignored"
    ;;
  *)
    die "unknown cadence type '$cadence_type' (use interval|daily|startup)"
    ;;
esac

record="${cadence_type}|${cadence_value}|${script_path}"

touch "$CONFIG_FILE"
if grep -Fxq "$record" "$CONFIG_FILE"; then
  echo "Already registered: $record"
  exit 0
fi

# Make sure the file ends with a newline before we append.
if [[ -s "$CONFIG_FILE" && -n "$(tail -c1 "$CONFIG_FILE")" ]]; then
  echo >> "$CONFIG_FILE"
fi
echo "$record" >> "$CONFIG_FILE"

echo "Registered: $record"
echo "Now commit scripts.conf; the next minute tick will pick it up."
