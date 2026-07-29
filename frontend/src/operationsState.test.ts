// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const operations = readFileSync(
  new URL("./Operations.tsx", import.meta.url),
  "utf8",
);
const microstructure = readFileSync(
  new URL("./MicrostructureResearch.tsx", import.meta.url),
  "utf8",
);
const asyncResource = readFileSync(
  new URL("./asyncResource.ts", import.meta.url),
  "utf8",
);

describe("production operations presentation contracts", () => {
  it("uses one public summary and never stores an admin token", () => {
    expect(operations).toContain("/api/operations/summary");
    expect(operations).not.toContain("sessionStorage");
    expect(operations).not.toContain("crypto_bot_admin_token");
  });

  it("does not render STOPPED before an explicit scheduler response", () => {
    expect(operations).toContain('summary ? (summary.scheduler.running ? "RUNNING" : "STOPPED") : "加载中"');
  });

  it("normalizes the real index instrument instead of leaving permanent dashes", () => {
    expect(microstructure).toContain('instrument.replace(/-SWAP$/, "")');
  });

  it("cancels superseded requests and rejects old responses", () => {
    expect(asyncResource).toContain("controller.current?.abort()");
    expect(asyncResource).toContain("requestGeneration !== generation.current");
    expect(asyncResource).toContain("for (let attempt = 0; attempt < 2");
  });
});
