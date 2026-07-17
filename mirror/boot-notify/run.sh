#!/bin/bash
# boot-notify entry point — registered in schedrunner as:
#   startup|ignored|/Users/joemoser/Dropbox/Source/boot-notify/run.sh
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/run.py"
