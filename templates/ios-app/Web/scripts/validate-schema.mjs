// Schema contract test: the golden manifest must validate against the shared
// JSON Schema that BOTH renderers (web + native) are built to. This is the guard
// that keeps the TypeScript and Swift renderers honest about one contract.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const here = dirname(fileURLToPath(import.meta.url));
const schemaDir = resolve(here, "..", "..", "Shared", "schema");
const schema = JSON.parse(readFileSync(resolve(schemaDir, "manifest.schema.json"), "utf8"));
const golden = JSON.parse(readFileSync(resolve(schemaDir, "golden-manifest.json"), "utf8"));

const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);
const validate = ajv.compile(schema);

if (!validate(golden)) {
  console.error("✗ golden-manifest.json does NOT conform to manifest.schema.json");
  console.error(validate.errors);
  process.exit(1);
}
console.log("✓ golden-manifest.json conforms to manifest.schema.json");
