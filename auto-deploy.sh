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
# Deploy outcome is also reported back to GitHub as a commit status on the
# deployed SHA (context "deploy/<hostname>"), so success/failure is visible on
# the commit and on PRs. This is best-effort: it requires `gh` authenticated and
# a GitHub remote, and never blocks or fails a deploy if unavailable.
#
# Called by schedrunner every 1 minute. Output captured by schedrunner and
# written to schedrunner/log/auto-deploy.sh.log.

set -uo pipefail

# Include /opt/homebrew/bin so `gh` (Apple Silicon Homebrew prefix) is on PATH
# under launchd, which starts with a minimal environment. Without this the
# commit-status reporting below silently no-ops because `gh` isn't found.
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SOURCE_DIR="$HOME/Dropbox/Source"
LOCK="/tmp/auto-deploy-poll.lock"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

# GitHub commit-status context, namespaced by host so multiple deploy machines
# don't clobber each other's status.
DEPLOY_CONTEXT="deploy/$(hostname -s 2>/dev/null || hostname)"

# report_status <sha> <state> <description>
# Posts a commit status to the current repo's <sha>. Best-effort: skips quietly
# if gh is missing/unauthenticated or the repo has no GitHub remote. gh resolves
# {owner}/{repo} from the current directory (we are cd'd into the repo).
report_status() {
    if ! command -v gh >/dev/null 2>&1; then
        echo "[$(ts)] $name: github status skipped (gh not found on PATH)"
        return 0
    fi
    if gh api -X POST "repos/{owner}/{repo}/statuses/$1" \
            -f "state=$2" \
            -f "context=$DEPLOY_CONTEXT" \
            -f "description=$(printf '%.140s' "$3")" >/dev/null 2>&1; then
        echo "[$(ts)] $name: github status -> $2"
    else
        echo "[$(ts)] $name: github status skipped (no gh auth or non-GitHub remote)"
    fi
}

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
    report_status "$remote_sha" pending "Deploying ${local_sha:0:7} -> ${remote_sha:0:7} on $DEPLOY_CONTEXT"

    echo "[$(ts)] $name: resetting working tree to remote"
    if ! git reset --hard FETCH_HEAD 2>&1; then
        echo "[$(ts)] $name: reset FAILED"
        report_status "$remote_sha" error "git reset to ${remote_sha:0:7} failed"
        continue
    fi
    echo "[$(ts)] $name: reset complete"

    # Run .auto-deploy as a bash script if non-empty
    if [[ -s "$repo/.auto-deploy" ]]; then
        echo "[$(ts)] $name: running .auto-deploy"
        if bash "$repo/.auto-deploy" 2>&1; then
            echo "[$(ts)] $name: deploy OK"
            report_status "$remote_sha" success "Deployed ${remote_sha:0:7}"
            deployed=$((deployed + 1))
        else
            rc=$?
            echo "[$(ts)] $name: .auto-deploy FAILED (exit $rc)"
            report_status "$remote_sha" failure "Post-pull hook failed (exit $rc)"
        fi
    else
        echo "[$(ts)] $name: reset OK (no post-pull script)"
        report_status "$remote_sha" success "Reset to ${remote_sha:0:7} (no hook)"
        deployed=$((deployed + 1))
    fi
done < <(find "$SOURCE_DIR" -name ".auto-deploy" -not -path "*/.git/*" | xargs -I{} dirname {} | sort)

[[ $deployed -gt 0 ]] && echo "[$(ts)] $deployed repo(s) updated"

exit 0
