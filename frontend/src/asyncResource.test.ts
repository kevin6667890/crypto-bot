import { describe, expect, it } from "vitest";
import { failedPhase, initialPhase } from "./asyncResource";

describe("async resource state semantics", () => {
  it("distinguishes loading from a stale retained success", () => {
    expect(initialPhase(false)).toBe("LOADING");
    expect(initialPhase(true)).toBe("STALE_LAST_SUCCESS");
  });

  it("distinguishes permission, unavailable and stale failures", () => {
    expect(failedPhase(401, false)).toBe("PERMISSION_REQUIRED");
    expect(failedPhase(504, false)).toBe("UNAVAILABLE");
    expect(failedPhase(504, true)).toBe("STALE_LAST_SUCCESS");
  });
});
