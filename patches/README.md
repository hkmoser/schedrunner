# patches/

Staging area for changes to **other** ecosystem repos that were authored from a
session without push access to them. `mirror/` is generated and read-only, so a
fix written against a mirrored repo has nowhere else to live until it is applied
upstream.

Apply on the Mac, then delete the patch from here:

```bash
cd ~/Dropbox/Source/afm/findmypy      # the real repo, not mirror/
git checkout -b <branch>
git am < ~/Dropbox/Source/schedrunner/patches/<file>.patch
```

`git am` preserves the commit message; `git apply` if you'd rather stage the
changes and write your own.

## Open

| Patch | Repo | What it does |
|---|---|---|
| `findmypy-dead-session-hold.patch` | `hkmoser/findmypy` | Stops `afm_live_daemon` from re-calling `/accountLogin` on every launchd restart once Apple has declared the session dead (421). Adds `afm_session_hold.py` + 34 tests. |
