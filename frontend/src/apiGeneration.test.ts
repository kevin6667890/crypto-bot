// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { execFileSync } from "node:child_process";
// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { execPath } from "node:process";
import { describe, expect, it } from "vitest";

describe("local OpenAPI generation", () => {
  it("is reproducible and has a no-edit header", () => {
    execFileSync(execPath, ["scripts/api-check.mjs"], { cwd: new URL("..", import.meta.url), stdio: "pipe" });
    const generated = readFileSync(new URL("./api/generated.ts", import.meta.url), "utf8");
    expect(generated).toContain("AUTO-GENERATED");
    expect(generated).toContain("DO NOT EDIT");
  }, 20_000);

  it("covers required read contracts and explicit time units", () => {
    const schema = readFileSync(new URL("../openapi/openapi.json", import.meta.url), "utf8");
    for (const path of ["/api/research/microstructure/health", "/api/operations/summary", "/api/paper/flow/history/v1", "/api/research/microstructure/eligibility", "/api/data-coverage"]) {
      expect(schema).toContain(path);
    }
    expect(schema).toContain('"x-time-unit": "seconds"');
    expect(schema).toContain('"x-time-unit": "milliseconds"');
  });
});
