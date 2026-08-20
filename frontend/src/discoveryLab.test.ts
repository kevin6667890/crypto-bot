// @ts-expect-error Vitest runs this source contract in Node; the app bundle has no Node types.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./DiscoveryLab.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
const catalog = readFileSync(new URL("./i18n.tsx", import.meta.url), "utf8");

describe("automatic research product view", () => {
  it("shows scheduler, evidence summary, validation and recent cycles", () => {
    for (const token of [
      "scheduler_enabled", "next_due_at", "research_counts", "automatic-research-summary",
      "automatic-research-facts", "automatic-research-validation", "automatic-research-recent",
      "No candidate passed development eligibility",
    ]) expect(`${source}\n${catalog}`).toContain(token);
  });

  it("uses bilingual catalog keys and responsive product styles", () => {
    expect(source).toContain("useLanguage");
    expect(catalog).toContain('"autoResearch.nextRun"');
    expect(catalog).toContain('"autoResearch.noEligibilityTitle"');
    expect(styles).toContain("@media(max-width:620px)");
    expect(styles).toContain(".automatic-research-summary");
  });

  it("keeps the safety and score disclaimers explicit", () => {
    expect(catalog).toContain("LIVE TRADING DISABLED");
    expect(catalog).toContain("not probabilities");
  });
});
