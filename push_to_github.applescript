-- push_to_github.applescript
-- Run this in Script Editor on your Mac to initialize the schedrunner git repo
-- and push it to GitHub as hkmoser/schedrunner (public).
--
-- Prerequisites:
--   - GitHub CLI (gh) is installed and authenticated: gh auth status
--   - You are logged in as the hkmoser GitHub account (or gh is configured for it)
--
-- Click Run once. A dialog will report success or failure.

set repoPath to "/Users/joemoser/Dropbox/Source/schedrunner"
set gitName to "Joe Moser"
set gitEmail to "joe@joemoser.com"
set repoSlug to "hkmoser/schedrunner"

set shellScript to "
set -e
export PATH=\"/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH\"

cd " & quoted form of repoPath & "

# Remove stale git locks if any
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null || true

# Init repo if not already a git repo
if [ ! -d .git ]; then
    git init -b main
fi

# Set local git identity
git config user.name " & quoted form of gitName & "
git config user.email " & quoted form of gitEmail & "

# Stage all files (respects .gitignore)
git add -A

# Make initial commit only if there is nothing committed yet
if git rev-parse HEAD >/dev/null 2>&1; then
    echo 'Repository already has commits — skipping initial commit.'
else
    git commit -m 'Initial commit'
fi

# Create GitHub repo and push (idempotent: --source=. sets origin automatically)
gh repo create " & quoted form of repoSlug & " --public --source=. --remote=origin --push

echo 'Done.'
"

try
    set result to do shell script shellScript
    display dialog "schedrunner pushed to GitHub successfully!" & return & return & result buttons {"OK"} default button "OK" with title "push_to_github"
on error errMsg number errNum
    display dialog "Something went wrong (error " & errNum & "):" & return & return & errMsg buttons {"OK"} default button "OK" with title "push_to_github — ERROR" with icon stop
end try
