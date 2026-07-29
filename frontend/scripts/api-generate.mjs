import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const root = process.cwd();
const schema = resolve(root, "openapi", "openapi.json");
const output = resolve(root, process.argv.includes("--check") ? "src/api/generated.check.ts" : "src/api/generated.ts");
execFileSync(process.execPath, [resolve(root, "node_modules", "openapi-typescript", "bin", "cli.js"), schema, "-o", output], { stdio: "inherit" });

const header = `/**\n * AUTO-GENERATED from openapi/openapi.json by npm run api:generate.\n * DO NOT EDIT. Update the local schema and regenerate instead.\n */\n`;
const generated = readFileSync(output, "utf8").replace(/^\/\*\*[\s\S]*?\*\/\r?\n/, "");
writeFileSync(output, header + generated.replace(/\r\n/g, "\n"), "utf8");

if (process.argv.includes("--check")) {
  const expected = readFileSync(resolve(root, "src/api/generated.ts"), "utf8");
  if (expected !== header + generated.replace(/\r\n/g, "\n")) {
    throw new Error("Generated API types have drifted. Run npm run api:generate and commit the result.");
  }
}
