import { describe, expect, it } from "vitest";
// @ts-expect-error source contract test runs in Node.
import { readFileSync } from "node:fs";
import { resolveProductRoute } from "./ProductApp";

describe("intent-based product routing", () => {
  it("routes product pages and normalized detail paths", () => {
    expect(resolveProductRoute("/", "")).toEqual({ kind: "home" });
    expect(resolveProductRoute("/test-an-idea/", "")).toEqual({ kind: "test" });
    expect(resolveProductRoute("/tracking", "")).toEqual({ kind: "tracking" });
    expect(resolveProductRoute("/tracking/track%201", "")).toEqual({ kind: "track-detail", trackId: "track 1" });
    expect(resolveProductRoute("/what-changed", "")).toEqual({ kind: "changes" });
  });

  it("preserves legacy path and hash entry points", () => {
    expect(resolveProductRoute("/advanced", "")).toEqual({ kind: "advanced" });
    for (const [path, hash] of [["/advanced", "#workspace"], ["/advanced", "#market"], ["/", "#workspace"], ["/", "#market"],
      ["/market-state-v2", ""], ["/strategy-router-v2", ""], ["/shadow/ai-market-analysis", ""]]) {
      expect(resolveProductRoute(path, hash)).toEqual({ kind: "legacy" });
    }
  });

  it("keeps the product entry light and lazy-loads legacy infrastructure", () => {
    const source = readFileSync(new URL("./ProductApp.tsx", import.meta.url), "utf8");
    expect(source).toContain('lazy(() => import("./App"))');
    expect(source).not.toContain('from "./charts"');
    expect(source).not.toContain('from "./data"');
  });
});
