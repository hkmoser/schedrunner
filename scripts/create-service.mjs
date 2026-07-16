// scripts/create-service.mjs
// Create a new repo from a template and register it in repos.yaml.
// Usage: GITHUB_TOKEN=... REPO_OWNER=... \
//   node scripts/create-service.mjs --name my-thing --type collector
import { readFile, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";
import { parse, stringify } from "yaml";

const TEMPLATES = {
  collector: "template-data-collector",
  mcp: "template-mcp-connector",
  // ios: "template-ios-app",   // added later
};

const { values } = parseArgs({ options: {
  name: { type: "string" }, type: { type: "string" }, owner: { type: "string" },
}});
const { name, type } = values;
const owner = values.owner ?? process.env.REPO_OWNER;
const token = process.env.GITHUB_TOKEN;

if (!name || !type || !TEMPLATES[type]) {
  console.error("usage: --name <name> --type <collector|mcp>"); process.exit(1);
}
if (!token || !owner) { console.error("GITHUB_TOKEN + REPO_OWNER (or --owner) required"); process.exit(1); }

const template = TEMPLATES[type];

// 1. Create the repo from the template.
const res = await fetch(`https://api.github.com/repos/${owner}/${template}/generate`, {
  method: "POST",
  headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json",
             "user-agent": "schedrunner", "content-type": "application/json" },
  body: JSON.stringify({ owner, name, private: true, include_all_branches: false }),
});
if (!res.ok) { console.error(`generate failed: ${res.status} ${await res.text()}`); process.exit(1); }
const repo = await res.json();
console.log(`created ${repo.full_name}`);

// 2. Register in repos.yaml (index + mirror flag).
const manifest = parse(await readFile("repos.yaml", "utf8"));
manifest.repos ??= [];
if (manifest.repos.some((r) => r.name === name)) {
  console.log(`${name} already in manifest, skipping`);
} else {
  manifest.repos.push({
    name,
    type: type === "ios" ? "app" : "service",
    remote: repo.clone_url,
    mirror: true,
  });
  await writeFile("repos.yaml", stringify(manifest));
  console.log(`registered ${name} in repos.yaml`);
}

console.log("\nNext:");
console.log("  1. Commit repos.yaml on a branch and open a PR (check for an open PR first).");
console.log("  2. After merge, the next mirror sync surfaces the new repo under mirror/.");
console.log(`  3. Open a chat on ${name} and fill in REPO.md + service.yaml.`);
