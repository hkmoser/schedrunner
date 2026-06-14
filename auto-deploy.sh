#!/bin/bash
# auto-deploy.sh
#
# Scans every git repo under ~/Dropbox/Source/ that contains a .auto-deploy
# flag file. For each flagged repo, checks whether origin/main has advanced
# beyond HEAD; if so, resets to remote and runs the post-pull script.
#
# .auto-deploy is a bash script that runs after a successful reset:
#   - Empty file  → just reset, no post-pull step
#   - Script      → run with `bash .auto-deploy` from the repo root
#
# Examples:
#   touch .auto-deploy                       # reset only
#   echo 'make update' > .auto-deploy        # reset + make update
#   # multi-line .auto-deploy:
#   #!/bin/bash
#   sed -i '' 's#foo#bar#' some/file.sh
#   make update
#
# The reset uses `git fetch` + `git reset --hard FETCH_HEAD`, which always
# produces a clean remote state regardless of local staged/unstaged changes.
#
# Called by schedrunner every 1 minute. Output captured by schedrunner and
# written to schedrunner/log/auto-deploy.sh.log.

set -uo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SOURCE_DIR="$HOME/Dropbox/Source"
LOCK="/tmp/auto-deploy-poll.lock"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Single-instance guard (macOS-compatible)
if [[ -f "$LOCK" ]]; then
    pid=$(cat "$LOCK")
    if kill -0 "$pid" 2>/dev/null; then
        echo "[$(ts)] already running (pid $pid) — skipping"
        exit 0
    fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT INT TERM

deployed=0

while IFS= read -r repo; do
    name="${repo#"$SOURCE_DIR/"}"
    cd "$repo" || continue

    # Detect default branch: prefer the symbolic HEAD pointer, fall back to master/main
    branch=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's#refs/remotes/origin/##')
    if [[ -z "$branch" ]]; then
        if git show-ref --quiet refs/remotes/origin/master; then
            branch="master"
        else
            branch="main"
        fi
    fi

    echo "[$(ts)] $name: fetching origin/$branch"
    git fetch origin "$branch" 2>&1 || { echo "[$(ts)] $name: fetch FAILED"; continue; }

    local_sha=$(git rev-parse HEAD 2>/dev/null)          || continue
    remote_sha=$(git rev-parse FETCH_HEAD 2>/dev/null)   || { echo "[$(ts)] $name: FETCH_HEAD not found"; continue; }

    if [[ "$local_sha" == "$remote_sha" ]]; then
        echo "[$(ts)] $name: up to date (${local_sha:0:7})"
        continue
    fi

    echo "[$(ts)] $name: new commits detected (${local_sha:0:7} → ${remote_sha:0:7})"
    echo "[$(ts)] $name: resetting working tree to remote"
    git reset --hard FETCH_HEAD 2>&1 || { echo "[$(ts)] $name: reset FAILED"; continue; }
    echo "[$(ts)] $name: reset complete"

    # Run .auto-deploy as a bash script if non-empty
    if [[ -s "$repo/.auto-deploy" ]]; then
        echo "[$(ts)] $name: running .auto-deploy"
        bash "$repo/.auto-deploy" 2>&1 \
            && { echo "[$(ts)] $name: deploy OK"; deployed=$((deployed + 1)); } \
            || echo "[$(ts)] $name: .auto-deploy FAILED (exit $?)"
    else
        echo "[$(ts)] $name: reset OK (no post-pull script)"
        deployed=$((deployed + 1))
    fi
done < <(find "$SOURCE_DIR" -name ".auto-deploy" -not -path "*/.git/*" | xargs -I{} dirname {} | sort)

[[ $deployed -gt 0 ]] && echo "[$(ts)] $deployed repo(s) updated"

exit 0
