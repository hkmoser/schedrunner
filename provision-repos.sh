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
# Register format (| delimited):  name|visibility|type|description|autodeploy|source
#   visibility: private (default) | public
#   type:       generic (default) | python | node   (controls .gitignore)
#   autodeploy: on (default) | off  — when on, an empty .auto-deploy flag is
#               committed so schedrunner keeps the repo in sync on the Mac
#   source:     optional. When set (a repo name or owner/repo), the new repo is
#               a CLEAN COPY of that repo's current snapshot (no fork link, no
#               history) instead of a fresh scaffold. type is ignored for copies.

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

create_and_push() {  # create_and_push <name> <visibility> <description>  (cwd = repo with a commit)
  local args=("$OWNER/$1" --source=. --remote=origin --push)
  [[ "$2" == "public" ]] && args+=(--public) || args+=(--private)
  [[ -n "$3" ]] && args+=(--description "$3")
  gh repo create "${args[@]}"
}

copy_repo() {  # copy_repo <name> <source> <visibility> <autodeploy> <description>
  local rname="$1" src="$2" rvis="$3" autod="$4" rdesc="$5" src_full work repo_dir
  case "$src" in */*) src_full="$src" ;; *) src_full="$OWNER/$src" ;; esac

  if ! gh repo view "$src_full" >/dev/null 2>&1; then
    echo "[$(ts)] $rname: source repo '$src_full' not found — skipping"
    return 1
  fi

  work="$(mktemp -d)"
  echo "[$(ts)] $rname: cloning snapshot of $src_full"
  if ! gh repo clone "$src_full" "$work" -- --depth 1 --quiet 2>&1; then
    echo "[$(ts)] $rname: clone of $src_full FAILED — skipping"
    rm -rf "$work"; return 1
  fi

  # Clean copy: drop history, start a fresh repo from the current snapshot.
  rm -rf "$work/.git"
  repo_dir="$SOURCE_DIR/$rname"
  rm -rf "$repo_dir"; mkdir -p "$repo_dir"
  cp -R "$work/." "$repo_dir/"
  rm -rf "$work"

  cd "$repo_dir" || { echo "[$(ts)] $rname: cannot cd to $repo_dir"; return 1; }
  git init -q
  if [[ "$autod" == "off" ]]; then rm -f .auto-deploy; else : > .auto-deploy; fi
  git add -A
  git diff --cached --quiet || git commit -qm "Initial copy of $src_full"

  if create_and_push "$rname" "$rvis" "$rdesc"; then
    echo "[$(ts)] $rname: copied $src_full -> $OWNER/$rname"
    return 0
  fi
  echo "[$(ts)] $rname: gh repo create FAILED"
  return 1
}

created=0
while IFS='|' read -r name visibility type description autodeploy source || [[ -n "${name:-}" ]]; do
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
  source="$(echo "${source:-}" | xargs)"

  if gh repo view "$OWNER/$name" >/dev/null 2>&1; then
    echo "[$(ts)] $name: already exists on GitHub — skipping"
    continue
  fi

  # Copy mode: duplicate an existing repo's snapshot instead of scaffolding.
  if [[ -n "$source" ]]; then
    echo "[$(ts)] $name: provisioning as copy of '$source' ($visibility, auto-deploy=$autodeploy)"
    copy_repo "$name" "$source" "$visibility" "$autodeploy" "$description" && created=$((created + 1))
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

  if create_and_push "$name" "$visibility" "$description"; then
    echo "[$(ts)] $name: created and pushed to $OWNER/$name"
    created=$((created + 1))
  else
    echo "[$(ts)] $name: gh repo create FAILED"
  fi
done < "$REGISTER"

echo "[$(ts)] provisioning complete ($created created)"
exit 0
