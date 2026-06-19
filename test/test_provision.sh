#!/bin/bash
# test_provision.sh — provision-repos.sh reads repos.register and, for entries
# whose repo does not yet exist, scaffolds + commits + creates the repo. The
# only external service, gh, is stubbed (see lib.sh); everything else is real:
# we run the REAL provision-repos.sh against a temp register + source dir.
source "$(dirname "$0")/lib.sh"

rm -f /tmp/provision-repos.lock

# run_provision <register_file> <source_dir>   (gh stub configured via env)
run_provision() {
  SCHEDRUNNER_REGISTER="$1" SCHEDRUNNER_SOURCE_DIR="$2" \
    bash "$REPO_ROOT/provision-repos.sh" 2>&1
}

# --- happy path: scaffold a brand-new repo ----------------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"; log="$(make_tmpdir)/gh.log"
printf 'newrepo|private|generic|A demo repo.|on\n' > "$reg"
out="$(GH_STUB_LOG="$log" GH_STUB_EXISTING="" run_provision "$reg" "$src")"
assert_contains "$out" "created and pushed to testuser/newrepo" "scaffold: reports creation"
assert_file "$src/newrepo/CLAUDE.md"   "scaffold: CLAUDE.md written"
assert_file "$src/newrepo/README.md"   "scaffold: README.md written"
assert_file "$src/newrepo/.gitignore"  "scaffold: .gitignore written"
assert_file "$src/newrepo/.auto-deploy" "scaffold: .auto-deploy flag written (autodeploy=on)"
assert_file "$src/newrepo/.git/HEAD"   "scaffold: git repo initialized"
assert_contains "$(cat "$log")" "repo create testuser/newrepo" "scaffold: gh repo create invoked"
assert_contains "$(cat "$log")" "--private" "scaffold: created as private"

# --- edge: autodeploy=off omits the .auto-deploy flag -----------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"
printf 'norepo|private|generic|No auto-deploy.|off\n' > "$reg"
run_provision "$reg" "$src" >/dev/null
assert_file "$src/norepo/CLAUDE.md" "autodeploy=off: still scaffolds files"
assert_no_file "$src/norepo/.auto-deploy" "autodeploy=off: no .auto-deploy flag"

# --- edge: type=python produces a Python .gitignore -------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"
printf 'pyrepo|private|python|Python project.|on\n' > "$reg"
run_provision "$reg" "$src" >/dev/null
assert_contains "$(cat "$src/pyrepo/.gitignore")" "__pycache__" "type=python: gitignore has Python section"

# --- edge: a repo that already exists is skipped ----------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"; log="$(make_tmpdir)/gh.log"
printf 'exists|private|generic|Already there.|on\n' > "$reg"
out="$(GH_STUB_LOG="$log" GH_STUB_EXISTING="testuser/exists" run_provision "$reg" "$src")"
assert_contains "$out" "already exists on GitHub — skipping" "existing repo: skipped"
assert_no_file "$src/exists" "existing repo: no local dir scaffolded"
assert_not_contains "$(cat "$log")" "repo create" "existing repo: gh repo create NOT called"

# --- edge: malformed name is rejected ---------------------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"
printf 'bad name|private|generic|Has a space.|on\n' > "$reg"
out="$(run_provision "$reg" "$src")"
assert_contains "$out" "skipping malformed entry" "malformed name: skipped with message"

# --- edge: comments and blank lines are ignored -----------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"
printf '# a comment\n\nokrepo|private|generic|Fine.|on\n' > "$reg"
out="$(run_provision "$reg" "$src")"
assert_contains "$out" "created and pushed to testuser/okrepo" "comments/blank: only the valid entry is provisioned"

# --- error: gh not authenticated aborts -------------------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"
printf 'whatever|private|generic|x.|on\n' > "$reg"
out="$(GH_STUB_LOGIN="" run_provision "$reg" "$src")"; rc=$?
assert_status 1 "$rc" "unauthenticated gh: exits non-zero"
assert_contains "$out" "gh not authenticated" "unauthenticated gh: explains why"

# --- error: gh repo create failure is reported ------------------------------
src="$(make_tmpdir)"; reg="$(make_tmpdir)/register"
printf 'failrepo|private|generic|x.|on\n' > "$reg"
out="$(GH_STUB_CREATE_RC=1 run_provision "$reg" "$src")"
assert_contains "$out" "gh repo create FAILED" "create failure: reported"

finish
