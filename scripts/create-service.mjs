// scripts/create-service.mjs
// Create a new repo from a local template and register it in repos.yaml.
// Usage: GITHUB_TOKEN=... REPO_OWNER=... \
//   node scripts/create-service.mjs --name my-thing --type collector [--dest ~/Dropbox/Source]
import { readFile, writeFile, cp } from "node:fs/promises";
import { execSync } from "node:child_process";
import { parseArgs } from "node:util";
import { parse, stringify } from "yaml";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const TEMPLATES = {
  collector: "data-collector",
  mcp:       "mcp-connector",
  ios:       "ios-app",
  site:      "cf-static-site",
};

const REPO_TYPE = {
  collector: "service",
  mcp:       "service",
  ios:       "app",
  site:      "app",
};

const { values } = parseArgs({ options: {
  name:  { type: "string" },
  type:  { type: "string" },
  owner: { type: "string" },
  dest:  { type: "string" },
}});
const { name, type } = values;
const owner = values.owner ?? process.env.REPO_OWNER;
const token = process.env.GITHUB_TOKEN;
const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(scriptDir, "..");

if (!name || !type || !TEMPLATES[type]) {
  console.error("usage: --name <name> --type <collector|mcp|ios|site>"); process.exit(1);
}
if (!token || !owner) { console.error("GITHUB_TOKEN + REPO_OWNER (or --owner) required"); process.exit(1); }

const templateDir = join(repoRoot, "templates", TEMPLATES[type]);
const destBase = values.dest ?? join(process.env.HOME, "Dropbox", "Source");
const destDir = join(destBase, name);

// 1. Copy template files to destination.
console.log(`copying templates/${TEMPLATES[type]} -> ${destDir}`);
await cp(templateDir, destDir, { recursive: true });

// 2. Create the GitHub repo.
console.log(`creating GitHub repo ${owner}/${name}`);
const res = await fetch(`https://api.github.com/user/repos`, {
  method: "POST",
  headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json",
             "user-agent": "schedrunner", "content-type": "application/json" },
  body: JSON.stringify({ name, private: true, auto_init: false }),
});
if (!res.ok) { console.error(`create repo failed: ${res.status} ${await res.text()}`); process.exit(1); }
const repo = await res.json();
console.log(`created ${repo.full_name}`);

// 3. Init, commit, push.
const run = (cmd) => execSync(cmd, { cwd: destDir, stdio: "inherit" });
run("git init -b main");
run("git add -A");
run(`git commit -m "chore: init from schedrunner template/${TEMPLATES[type]}"`);
run(`git remote add origin ${repo.ssh_url}`);
run("git push -u origin main");
console.log(`pushed initial commit to ${repo.ssh_url}`);

// 4. Register in repos.yaml.
const manifestPath = join(repoRoot, "repos.yaml");
const manifest = parse(await readFile(manifestPath, "utf8"));
manifest.repos ??= [];
if (manifest.repos.some((r) => r.name === name)) {
  console.log(`${name} already in manifest, skipping`);
} else {
  manifest.repos.push({ name, type: REPO_TYPE[type], remote: repo.ssh_url, mirror: true });
  await writeFile(manifestPath, stringify(manifest));
  console.log(`registered ${name} in repos.yaml`);
}

console.log("\nNext:");
console.log("  1. Commit repos.yaml on a branch and open a PR (check for an open PR first).");
console.log("  2. After merge, the next mirror sync surfaces the new repo under mirror/.");
console.log(`  3. Open a chat on ${name} and fill in REPO.md + service.yaml.`);
