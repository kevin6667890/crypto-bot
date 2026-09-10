// @ts-expect-error Source-contract tests run in Node without Node types in the browser tsconfig.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const productApp = readFileSync(new URL("./ProductApp.tsx", import.meta.url), "utf8");
const productShell = readFileSync(new URL("./product/ProductShell.tsx", import.meta.url), "utf8");
const landing = readFileSync(new URL("./product/AdvancedLanding.tsx", import.meta.url), "utf8");
const secondary = readFileSync(new URL("./AdvancedSecondaryNav.tsx", import.meta.url), "utf8");
const legacyApp = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("./product.css", import.meta.url), "utf8");

describe("advanced product shell", () => {
  it("keeps the landing and legacy workbench lazy at the product boundary", () => {
    expect(productApp).toContain('lazy(() => import("./product/AdvancedLanding"))');
    expect(productApp).toContain('lazy(() => import("./App"))');
    expect(productApp).toContain('path === "/advanced" && !legacyHash');
  });

  it("offers all five advanced destinations from the dark landing", () => {
    for (const destination of ["workspace", "market", "research", "microstructure", "operations"]) {
      expect(landing).toContain(`id: "${destination}"`);
    }
    expect(landing).toContain("advanced.${id}.title");
    expect(landing).toContain("advanced-entry-grid");
    expect(landing).toContain('href={`/advanced#${id}`}');
  });

  it("keeps Advanced in the global navigation system while preserving hash navigation", () => {
    expect(productShell).toContain('page: "advanced"');
    expect(productShell).toContain("workflow-navigation");
    expect(productShell).not.toContain("advanced-nav-accent");
    expect(secondary).toContain("advanced-secondary-nav");
    expect(secondary).toContain('href={`/advanced#${page}`}');
    expect(secondary).toContain('aria-current={active === page ? "page"');
    expect(legacyApp).toContain("<AdvancedSecondaryNav active={activePage}");
    expect(legacyApp).toContain('route.includes("market-state-v2")');
    expect(legacyApp).toContain('route.includes("strategy-router-v2")');
  });

  it("keeps the advanced visual treatment quiet", () => {
    expect(styles).not.toContain("advanced-ring-flow");
    expect(styles).not.toContain("background-position: 240% 50%");
    expect(styles).toContain(".workflow-navigation a.active");
    expect(styles).toContain(":focus-visible");
  });
});
