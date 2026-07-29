import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { resolve } from "node:path";

const temporary = resolve(process.cwd(), "src/api/generated.check.ts");
try {
  execFileSync(process.execPath, ["scripts/api-generate.mjs", "--check"], { stdio: "inherit" });
} finally {
  rmSync(temporary, { force: true });
}
