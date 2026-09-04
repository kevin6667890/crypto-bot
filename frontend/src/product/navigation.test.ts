import { describe, expect, it } from "vitest";
// @ts-expect-error Source contract test runs in Node.
import { readFileSync } from "node:fs";
import { navigationState } from "./navigation";

const shell = readFileSync(new URL("./ProductShell.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../product.css", import.meta.url), "utf8");

describe("global navigation state", () => {
  it("keeps the Crypto home domain-selected without an active workflow page", () => {
    expect(navigationState("home")).toEqual({ domain: "crypto" });
  });

  it.each([
    ["test", "test"], ["tracking", "tracking"], ["track-detail", "tracking"],
    ["changes", "changes"], ["advanced", "advanced"],
  ])("maps %s to its one active workflow destination", (active, page) => {
    expect(navigationState(active)).toEqual({ domain: "crypto", page });
  });

  it("does not expose Crypto workflow navigation in Prediction Markets", () => {
    expect(navigationState("prediction-markets")).toEqual({ domain: "prediction-markets" });
  });

  it("uses one page-active system and reserves focus rings for keyboard focus", () => {
    expect(shell).toContain('aria-current={state.page === page ? "page" : undefined}');
    expect(shell).toContain('state.domain === "crypto" && <div className="workflow-navigation"');
    expect(styles).toContain(".workflow-navigation a.active");
    expect(styles).toContain(":focus-visible");
    expect(styles).not.toContain(".product-header nav a.primary");
  });
});
