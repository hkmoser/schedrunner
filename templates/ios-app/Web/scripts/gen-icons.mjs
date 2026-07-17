// Rasterize the source SVG into the PNG sizes the PWA + iOS home screen need.
// iOS does not accept SVG for apple-touch-icon, so PNGs are required. Uses
// @resvg/resvg-js (prebuilt binaries for darwin-arm64 and linux-x64 — works on
// both the Mac mini and CI), so there's no native toolchain dependency.
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Resvg } from "@resvg/resvg-js";

const here = dirname(fileURLToPath(import.meta.url));
const iconsDir = resolve(here, "..", "public", "icons");
const svg = readFileSync(resolve(iconsDir, "favicon.svg"), "utf8");

const targets = [
  { file: "icon-192.png", size: 192 },
  { file: "icon-512.png", size: 512 },
  { file: "apple-touch-icon.png", size: 180 },
];

if (!existsSync(iconsDir)) mkdirSync(iconsDir, { recursive: true });

for (const { file, size } of targets) {
  const resvg = new Resvg(svg, { fitTo: { mode: "width", value: size } });
  const png = resvg.render().asPng();
  writeFileSync(resolve(iconsDir, file), png);
  console.log(`generated icons/${file} (${size}px)`);
}
