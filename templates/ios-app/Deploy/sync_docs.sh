#!/usr/bin/env bash
# Mirror the Google Drive /Private docs tree (folders + Markdown) into a local
# directory the sidecar reads, so Docs is ALWAYS available — even when a file is
# "online-only" in Google Drive, the Drive app is closed, or you're off Tailscale.
# rsync reads each file's bytes, which forces Drive for Desktop to materialize it,
# leaving a real local copy behind. Safe to run repeatedly (idempotent) and on a
# timer (e.g. via schedrunner). Non-fatal if Drive isn't found.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

MIRROR="${DOCS_MIRROR:-$HOME/.cache/__APP_NAME_LOWER__/docs}"
MIRROR="${MIRROR/#\~/$HOME}"

# Resolve the live /Private source: DOCS_DIR wins, else the Drive for Desktop mount.
src=""
if [[ -n "${DOCS_DIR:-}" && -d "${DOCS_DIR}" ]]; then
  src="$DOCS_DIR"
else
  for base in "$HOME/Library/CloudStorage"/GoogleDrive-*/"My Drive" "$HOME/CloudStorage"/GoogleDrive-*/"My Drive"; do
    [[ -d "$base/Private" ]] && { src="$base/Private"; break; }
  done
fi
if [[ -z "$src" || ! -d "$src" ]]; then
  warn "Google Drive /Private not found (set DOCS_DIR) — skipping docs mirror"
  exit 0
fi

mkdir -p "$MIRROR"
c_bold "== mirror docs =="
info "$src -> $MIRROR"
# Folders + Markdown only; reading bytes materializes online-only Drive placeholders.
# --delete keeps the mirror in lockstep; --prune-empty-dirs drops folders with no docs.
rsync -rtL --delete --prune-empty-dirs \
  --include='*/' --include='*.md' --include='*.markdown' --exclude='*' \
  "$src/" "$MIRROR/" || { warn "rsync reported an issue; mirror may be partial"; exit 0; }

count="$(find "$MIRROR" -type f \( -name '*.md' -o -name '*.markdown' \) 2>/dev/null | wc -l | tr -d ' ')"
ok "docs mirror synced — $count Markdown files available locally"
