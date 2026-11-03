#!/bin/bash

security -i unlock-keychain

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
/Users/joemoser/Dropbox/Source/afm/findmypy/.venv/bin/python "$SCRIPT_DIR/auth.py" "$@"
STATUS=$?

if [ "$STATUS" -eq 0 ]; then
  rm -f ~/.pyicloud-accountlogin-holdoff
  echo "[auth.sh] /accountLogin holdoff cleared"
else
  echo "[auth.sh] auth.py exited $STATUS — live session and holdoff left untouched"
fi

exit "$STATUS"
