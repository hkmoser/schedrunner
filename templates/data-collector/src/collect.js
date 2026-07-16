// Minimal data collector: fetch a source, append a timestamped markdown entry.
// The scheduled wrapper (schedrunner) commits + pushes the data/ output.
import { writeFile, mkdir } from "node:fs/promises";

const SOURCE = process.env.SOURCE_URL ?? "https://api.example.com/data";

async function main() {
  const res = await fetch(SOURCE);
  if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
  const data = await res.json();

  const now = new Date();
  const day = now.toISOString().slice(0, 10);
  await mkdir("data", { recursive: true });
  const entry = `## ${now.toISOString()}\n\n\`\`\`json\n${JSON.stringify(data, null, 2)}\n\`\`\`\n\n`;
  await writeFile(`data/${day}.md`, entry, { flag: "a" });
  console.log(`wrote data/${day}.md`);
}

main().catch((err) => { console.error(err); process.exit(1); });
