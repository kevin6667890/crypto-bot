import { describe, expect, it } from "vitest";
// @ts-expect-error source contract test runs in Node.
import { readFileSync } from "node:fs";
import { conditionExpression, conditionTone, formatObserved, formatStatus, requiredConditionSummary, statusTone } from "./state";

describe("tracked thesis presentation semantics", () => {
  it("formats the saved definition without evaluating it", () => {
    expect(conditionExpression({ feature: "VOLUME_RATIO", operator: "gte", value: 1.2 })).toBe("VOLUME RATIO ≥ 1.2");
    expect(conditionExpression({ feature: "PRICE_ABOVE_MA200", operator: "eq", value: true })).toBe("PRICE ABOVE MA200 = true");
  });

  it("keeps three-valued condition states visually distinct", () => {
    expect(conditionTone("TRUE")).toBe("matching");
    expect(conditionTone("FALSE")).toBe("not-matching");
    expect(conditionTone("UNKNOWN")).toBe("unknown");
    expect(conditionTone("UNKNOWN")).not.toBe(conditionTone("FALSE"));
  });

  it("never presents unknown required evidence as a zero-match result", () => {
    const evaluation = {
      required_match_count: 0,
      required_condition_count: 2,
      conditions: [
        { requirement: "REQUIRED", state: "UNKNOWN" },
        { requirement: "REQUIRED", state: "UNKNOWN" },
      ],
    } as any;
    expect(requiredConditionSummary(evaluation, "en")).toBe("0 true, 2 unknown of 2 required conditions");
    expect(requiredConditionSummary(evaluation, "en")).not.toContain("0 / 2");
  });

  it("does not call a non-match an invalidation or create a score", () => {
    expect(formatStatus("NOT_MATCHING")).toBe("NOT MATCHING");
    expect(statusTone("STALE")).toBe("warning");
    expect(statusTone("BLOCKED_VERSION_MISMATCH")).toBe("blocked");
    expect(formatObserved(null)).toBe("—");
  });

  it("uses one domain API for manual evaluation and consumes real MarketState transitions", () => {
    const api = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
    const changes = readFileSync(new URL("./WhatChangedPage.tsx", import.meta.url), "utf8");
    expect(api).toContain('version: "current-thesis-evaluate-request-v1"');
    expect(api).toContain('version: "track-thesis-archive-v1"');
    expect(api).toContain("market_state_changes");
    expect(changes).toContain("MARKET STRUCTURE CHANGED");
    expect(changes).toContain("transition.trigger_evidence");
    expect(changes).not.toMatch(/\bAI\b.*changed/i);
  });
});
