#!/bin/bash
# test_syntax.sh — every shell script in the repo must parse (`bash -n`).
# Cheap regression guard against syntax errors slipping into the deploy scripts.
source "$(dirname "$0")/lib.sh"

while IFS= read -r script; do
  if bash -n "$script" 2>/dev/null; then
    _ok "bash -n: ${script#"$REPO_ROOT"/}"
  else
    _fail "bash -n: ${script#"$REPO_ROOT"/}" "$(bash -n "$script" 2>&1)"
  fi
done < <(find "$REPO_ROOT" -name '*.sh' -not -path '*/.git/*' | sort)

finish
