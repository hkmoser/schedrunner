#!/bin/bash

security -i unlock-keychain

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
/Users/joemoser/Dropbox/Source/afm/findmypy/.venv/bin/python "$SCRIPT_DIR/auth.py"

# Clear the /accountLogin holdoff so the first post-auth.sh cron run calls
# /accountLogin cleanly and writes a fresh holdoff timestamp with the new session.
rm -f ~/.pyicloud-accountlogin-holdoff
echo "[auth.sh] /accountLogin holdoff cleared"
