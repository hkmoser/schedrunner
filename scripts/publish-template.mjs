// scripts/publish-template.mjs
// One-time: publish a local template folder as a private GitHub template repo.
// Usage: GITHUB_TOKEN=... REPO_OWNER=... \
//   node scripts/publish-template.mjs --dir templates/data-collector --repo template-data-collector
import { execFileSync } from "node:child_process";
import { parseArgs } from "node:util";
import { mkdtempSync, cpSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const { values } = parseArgs({ options: {
  dir: { type: "string" }, repo: { type: "string" }, owner: { type: "string" },
}});
const dir = values.dir, repo = values.repo;
const owner = values.owner ?? process.env.REPO_OWNER;
const token = process.env.GITHUB_TOKEN;
if (!dir || !repo || !owner || !token) {
  console.error("usage: --dir <folder> --repo <name> [--owner <owner>]; GITHUB_TOKEN + REPO_OWNER required");
  process.exit(1);
}

const api = (path, method, body) => fetch(`https://api.github.com${path}`, {
  method,
  headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json",
             "user-agent": "schedrunner", "content-type": "application/json" },
  body: body ? JSON.stringify(body) : undefined,
});

// 1. Create the repo (422 = already exists, that's fine).
// If owner is an org, use `/orgs/${owner}/repos` instead of `/user/repos`.
let res = await api(`/user/repos`, "POST", { name: repo, private: true });
if (!res.ok && res.status !== 422) { console.error(await res.text()); process.exit(1); }

// 2. Push the folder as the initial commit.
const work = mkdtempSync(join(tmpdir(), "tmpl-"));
cpSync(dir, work, { recursive: true });
const git = (...a) => execFileSync("git", a, { cwd: work, stdio: "inherit" });
git("init", "-b", "main");
git("add", ".");
git("-c", "user.email=schedrunner@local", "-c", "user.name=schedrunner", "commit", "-m", "chore: seed template");
git("remote", "add", "origin", `https://x-access-token:${token}@github.com/${owner}/${repo}.git`);
git("push", "-u", "origin", "main", "--force");

// 3. Mark it as a template repo (GitHub uses the is_template flag).
res = await api(`/repos/${owner}/${repo}`, "PATCH", { is_template: true });
if (!res.ok) { console.error(await res.text()); process.exit(1); }
console.log(`published ${owner}/${repo} as a template repo`);
