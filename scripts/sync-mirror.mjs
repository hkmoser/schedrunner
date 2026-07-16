// scripts/sync-mirror.mjs
// Read-only source mirror. For each repo with mirror:true in repos.yaml,
// refresh a shallow cache clone and rsync its source into mirror/<name>/.
// Commits + pushes schedrunner if anything changed. Runs on the Mac mini.
import { execFileSync } from "node:child_process";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { parse } from "yaml";

const sh = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, stdio: "pipe" }).toString().trim();
const run = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, stdio: "inherit" });

const manifest = parse(await readFile("repos.yaml", "utf8"));
const repos = (manifest.repos ?? []).filter((r) => r.mirror);

await mkdir(".cache", { recursive: true });
await mkdir("mirror", { recursive: true });
await writeFile(
  "mirror/README.md",
  "# mirror/\n\nGenerated read-only copies of ecosystem repos. Do not edit — changes " +
    "here are overwritten on the next sync and cannot reach the source repos.\n"
);

const EXCLUDES = [".git", "node_modules", "dist", "build", ".wrangler", ".next"];
const rsyncExcludes = EXCLUDES.flatMap((e) => ["--exclude", e]);

for (const r of repos) {
  const cache = `.cache/${r.name}`;
  if (existsSync(cache)) {
    run("git", ["-C", cache, "fetch", "--depth", "1", "origin"]);
    run("git", ["-C", cache, "reset", "--hard", "FETCH_HEAD"]);
  } else {
    run("git", ["clone", "--depth", "1", r.remote, cache]);
  }
  await mkdir(`mirror/${r.name}`, { recursive: true });
  run("rsync", ["-a", "--delete", ...rsyncExcludes, `${cache}/`, `mirror/${r.name}/`]);
}

const status = sh("git", ["status", "--porcelain", "mirror"]);
if (status) {
  run("git", ["add", "mirror"]);
  run("git", ["-c", "user.email=schedrunner@local", "-c", "user.name=schedrunner",
              "commit", "-m", "chore(mirror): sync ecosystem source [skip ci]"]);
  run("git", ["push"]);
  console.log("mirror updated");
} else {
  console.log("mirror already up to date");
}
