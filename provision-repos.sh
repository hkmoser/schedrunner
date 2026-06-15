#!/bin/bash
# provision-repos.sh
#
# Reads repos.register and, for each entry whose repo does NOT yet exist on
# GitHub, creates it: scaffolds starter files under ~/Dropbox/Source/<name>,
# commits, and creates + pushes the repo with `gh repo create ... --push`.
# Repos that already exist are skipped, so this is safe to run on a schedule
# (existence on GitHub is the gate — the register is never modified here).
#
# Registered in scripts.conf, e.g.:
#   interval|2|/Users/joemoser/Dropbox/Source/schedrunner/provision-repos.sh
#
# Requires: gh (authenticated) and git on the Mac. Output is captured by
# schedrunner to log/provision-repos.sh.log.
#
# Register format (| delimited):  name|visibility|type|description|autodeploy
#   visibility: private (default) | public
#   type:       generic (default) | python | node   (controls .gitignore)
#   autodeploy: on (default) | off  — when on, an empty .auto-deploy flag is
#               committed so schedrunner keeps the repo in sync on the Mac

set -uo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTER="${SCHEDRUNNER_REGISTER:-$SCRIPT_DIR/repos.register}"
TEMPLATES="$SCRIPT_DIR/.claude/skills/new-repo/templates"
SOURCE_DIR="${SCHEDRUNNER_SOURCE_DIR:-$HOME/Dropbox/Source}"
LOCK="/tmp/provision-repos.lock"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

[[ -f "$REGISTER" ]] || { echo "[$(ts)] no register at $REGISTER — nothing to do"; exit 0; }

command -v gh >/dev/null 2>&1 || { echo "[$(ts)] gh CLI not found — cannot provision"; exit 1; }
OWNER="$(gh api user --jq .login 2>/dev/null || true)"
[[ -n "$OWNER" ]] || { echo "[$(ts)] gh not authenticated — cannot provision"; exit 1; }

# Single-instance guard
if [[ -f "$LOCK" ]]; then
  pid=$(cat "$LOCK" 2>/dev/null || echo)
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[$(ts)] already running (pid $pid) — skipping"; exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

str_replace() {  # str_replace <haystack> <needle> <replacement> — literal, portable
  local hay="$1" needle="$2" repl="$3" out=""
  while [[ "$hay" == *"$needle"* ]]; do
    out+="${hay%%"$needle"*}$repl"
    hay="${hay#*"$needle"}"
  done
  printf '%s' "$out$hay"
}

render() {  # render <template> <name> <description> -> stdout
  local content; content="$(cat "$1")"
  content="$(str_replace "$content" "{{REPO_NAME}}" "$2")"
  content="$(str_replace "$content" "{{DESCRIPTION}}" "$3")"
  printf '%s\n' "$content"
}

write_gitignore() {  # write_gitignore <type>  (cwd = repo)
  {
    printf '# macOS\n.DS_Store\n\n# Logs\n*.log\n\n# Env / secrets\n.env\n.env.local\n'
    case "$1" in
      python) printf '\n# Python\n__pycache__/\n*.pyc\n*.pyo\n.venv/\n*.egg-info/\n' ;;
      node)   printf '\n# Node\nnode_modules/\ndist/\nnpm-debug.log*\n' ;;
    esac
  } > .gitignore
}

created=0
while IFS='|' read -r name visibility type description autodeploy || [[ -n "${name:-}" ]]; do
  name="$(echo "${name:-}" | xargs)"
  [[ -z "$name" || "$name" == \#* ]] && continue
  if ! [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "[$(ts)] skipping malformed entry: '$name'"
    continue
  fi

  visibility="$(echo "${visibility:-}" | xargs)"; visibility="${visibility:-private}"
  type="$(echo "${type:-}" | xargs)"; type="${type:-generic}"
  description="$(echo "${description:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  autodeploy="$(echo "${autodeploy:-}" | xargs)"; autodeploy="${autodeploy:-on}"

  if gh repo view "$OWNER/$name" >/dev/null 2>&1; then
    echo "[$(ts)] $name: already exists on GitHub — skipping"
    continue
  fi

  echo "[$(ts)] $name: provisioning ($visibility, $type, auto-deploy=$autodeploy)"
  repo_dir="$SOURCE_DIR/$name"
  mkdir -p "$repo_dir"
  cd "$repo_dir" || { echo "[$(ts)] $name: cannot cd to $repo_dir"; continue; }

  [[ -d .git ]] || git init -q

  render "$TEMPLATES/CLAUDE.md.tmpl" "$name" "$description" > CLAUDE.md
  render "$TEMPLATES/README.md.tmpl" "$name" "$description" > README.md
  write_gitignore "$type"

  # Turn on schedrunner auto-deploy for the new repo (empty flag = pull-only),
  # so the Mac keeps it in sync with origin from the start.
  if [[ "$autodeploy" != "off" ]]; then
    : > .auto-deploy
  fi

  git add -A
  git diff --cached --quiet || git commit -qm "Scaffold $name (schedrunner-aware)"

  create_args=("$OWNER/$name" --source=. --remote=origin --push)
  [[ "$visibility" == "public" ]] && create_args+=(--public) || create_args+=(--private)
  [[ -n "$description" ]] && create_args+=(--description "$description")

  if gh repo create "${create_args[@]}"; then
    echo "[$(ts)] $name: created and pushed to $OWNER/$name"
    created=$((created + 1))
  else
    echo "[$(ts)] $name: gh repo create FAILED"
  fi
done < "$REGISTER"

echo "[$(ts)] provisioning complete ($created created)"
exit 0
