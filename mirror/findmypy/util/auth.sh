#!/bin/bash

security -i unlock-keychain

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
/Users/joemoser/Dropbox/Source/afm/findmypy/.venv/bin/python "$SCRIPT_DIR/auth.py" "$@"
STATUS=$?

# Clear the /accountLogin holdoff so the first post-auth.sh cron run calls
# /accountLogin cleanly and writes a fresh holdoff timestamp with the new session.
# Only on success: if auth.py did not install a session, the live session is still
# the old one and its holdoff is the state that protects it from token burn.
# (auth.py clears this itself on install; this is belt-and-braces for older paths.)
if [ "$STATUS" -eq 0 ]; then
  rm -f ~/.pyicloud-accountlogin-holdoff
  echo "[auth.sh] /accountLogin holdoff cleared"
else
  echo "[auth.sh] auth.py exited $STATUS — live session and holdoff left untouched"
fi

exit "$STATUS"
