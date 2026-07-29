import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const assets = join(process.cwd(), "dist", "assets");
const limits = {
  entry: 300 * 1024,
  route: 300 * 1024,
  vendor: 350 * 1024,
};
const html = readFileSync(join(process.cwd(), "dist", "index.html"), "utf8");
const entryMatch = html.match(/src="\/?assets\/([^"]+\.js)"/);
if (!entryMatch) throw new Error("Unable to identify the main entry chunk");

const failures = [];
for (const file of readdirSync(assets).filter((name) => name.endsWith(".js"))) {
  const bytes = statSync(join(assets, file)).size;
  const kind = file === entryMatch[1] ? "entry" : file.includes("vendor") ? "vendor" : "route";
  const limit = limits[kind];
  console.log(`${kind.padEnd(6)} ${file.padEnd(42)} ${(bytes / 1024).toFixed(2)} KiB / ${(limit / 1024).toFixed(0)} KiB`);
  if (bytes > limit) failures.push(`${file}: ${bytes} > ${limit}`);
}
if (failures.length) {
  throw new Error(`Bundle budget exceeded:\n${failures.join("\n")}`);
}
