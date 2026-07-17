#!/usr/bin/env bash
# Set up the 'next' release channel: a git WORKTREE on branch `next` beside this repo, so the
# next stack builds from its own tree (separate Server/.build, Web/dist, build stamp) while the
# stable checkout keeps serving `main`. Idempotent — re-running just ensures the worktree + branch
# and (optionally) redeploys. The two stacks then run side by side:
#   stable  main   → :8080 / sidecar :8099 / TLS :443   → https://$TS_HOST/
#   next    next   → :8081 / sidecar :8100 / TLS :8443  → https://$TS_HOST:8443/
# Deploy the next stack from its worktree:  cd <worktree> && make deploy CHANNEL=next
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

WORKTREE="${NEXT_WORKTREE:-$(cd "$REPO_ROOT/.." && pwd)/iOS-Shell-next}"
BRANCH="${NEXT_BRANCH:-next}"

c_bold "== next channel setup =="

# 1) Ensure the `next` branch exists (track origin/next if present, else fork from origin/main).
if ! git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
  if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git -C "$REPO_ROOT" fetch origin "$BRANCH"
    git -C "$REPO_ROOT" branch "$BRANCH" "origin/$BRANCH"
    ok "branch '$BRANCH' created tracking origin/$BRANCH"
  else
    git -C "$REPO_ROOT" fetch origin main
    # --no-track: forking from origin/main must NOT leave the branch tracking origin/main —
    # that makes `git pull` in the worktree pull MAIN and `git push` error on the name
    # mismatch. Proper upstream (origin/next) is established below once the branch is pushed.
    git -C "$REPO_ROOT" branch --no-track "$BRANCH" origin/main
    ok "branch '$BRANCH' forked from origin/main"
  fi
else
  ok "branch '$BRANCH' already exists"
fi

# 2) Ensure the worktree.
if git -C "$REPO_ROOT" worktree list --porcelain | grep -q "worktree $WORKTREE"; then
  ok "worktree already present at $WORKTREE"
elif [[ -e "$WORKTREE" ]]; then
  fail "$WORKTREE exists but isn't a registered worktree — move it aside or set NEXT_WORKTREE"
else
  git -C "$REPO_ROOT" worktree add "$WORKTREE" "$BRANCH"
  ok "worktree added at $WORKTREE (branch $BRANCH)"
fi

# 2b) Make the branch track origin/next so `git pull`/`git push` in the worktree do the right
# thing. Publishes the branch on first run; also HEALS a worktree created by an older version
# of this script, which left `next` tracking origin/main (so `git pull` pulled main and
# `git push` errored on the upstream-name mismatch).
current_upstream="$(git -C "$WORKTREE" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [[ "$current_upstream" != "origin/$BRANCH" ]]; then
  if git -C "$REPO_ROOT" ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git -C "$WORKTREE" branch -u "origin/$BRANCH" "$BRANCH" \
      && ok "branch '$BRANCH' now tracks origin/$BRANCH (was: ${current_upstream:-none})"
  elif git -C "$WORKTREE" push -u origin "$BRANCH" 2>/dev/null; then
    ok "branch '$BRANCH' published and tracking origin/$BRANCH"
  else
    warn "couldn't publish '$BRANCH' to origin — run 'git push -u origin $BRANCH' in $WORKTREE when online"
  fi
fi

# 3) PIN the channel to the worktree. lib.sh reads Deploy/.channel before deriving ports/labels,
# so every command run in this tree — a bare `make update`, the auto-deploy timer firing from
# launchd's empty environment, a /deploy_kick — is 'next' even without CHANNEL=next. This is the
# isolation guarantee: nothing run from this tree can ever write stable's labels or ports.
printf 'next\n' > "$WORKTREE/Deploy/.channel"
ok "channel pinned: $WORKTREE/Deploy/.channel = next"

# 4) The next stack needs the same secrets — share Deploy/.env if it isn't there yet.
if [[ -f "$REPO_ROOT/Deploy/.env" && ! -f "$WORKTREE/Deploy/.env" ]]; then
  cp "$REPO_ROOT/Deploy/.env" "$WORKTREE/Deploy/.env"
  # The second warmer would double external-API usage (Twelve Data credits, etc.) on the same
  # keys. Warm next 5× less often by default — data still refreshes, stable keeps the budget.
  printf '\n# next channel: warm less aggressively so two stacks do not double external-API usage\nDASHBOARD_WARM_INTERVAL=300\n' >> "$WORKTREE/Deploy/.env"
  ok "copied Deploy/.env into the next worktree (with a gentler DASHBOARD_WARM_INTERVAL)"
fi

echo
c_bold "next steps"
info "deploy the next stack:   cd \"$WORKTREE\" && make deploy CHANNEL=next"
info "  (that brings up server :8081, sidecar :8100, and Tailscale serve on :8443)"
info "install on the phone:    open https://\$TS_HOST:8443/ in Safari → Add to Home Screen"
info "keep it self-healing:    (in the worktree)  make auto-deploy-timer CHANNEL=next"
info "check both stacks:       make channel-status"
