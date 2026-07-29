import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { failedPhase, initialPhase } from "./asyncResource";
import { boundedJson, FaultServiceLayer } from "./testing/faultServiceLayer";

let faults: FaultServiceLayer;
beforeEach(() => {
  faults = new FaultServiceLayer();
  faults.install();
});
afterEach(() => faults.restore());

describe("page fault simulation layer", () => {
  it("maps HTTP 500/504 and permission to distinct presentation states", async () => {
    faults.respond("/500", { status: 500 });
    faults.respond("/504", { status: 504 });
    faults.respond("/permission", { status: 403 });
    await expect(boundedJson("/500", 100)).rejects.toThrow("HTTP_500");
    await expect(boundedJson("/504", 100)).rejects.toThrow("HTTP_504");
    await expect(boundedJson("/permission", 100)).rejects.toThrow("HTTP_403");
    expect(failedPhase(500, false)).toBe("UNAVAILABLE");
    expect(failedPhase(504, false)).toBe("UNAVAILABLE");
    expect(failedPhase(403, false)).toBe("PERMISSION_REQUIRED");
  });

  it("does not let a slow card block another card", async () => {
    faults.respond("/slow", { delayMs: 80, body: { card: "slow" } });
    faults.respond("/fast", { body: { card: "fast" } });
    let slowReady = false;
    const slow = boundedJson<{ card: string }>("/slow", 200).then(value => {
      slowReady = true;
      return value;
    });
    const fast = await boundedJson<{ card: string }>("/fast", 200);
    expect(fast.card).toBe("fast");
    expect(slowReady).toBe(false);
    await expect(slow).resolves.toEqual({ card: "slow" });
  });

  it("simulates timeout without inventing STOPPED", async () => {
    faults.respond("/timeout", { delayMs: 100, body: { service: "RUNNING" } });
    await expect(boundedJson("/timeout", 10)).rejects.toHaveProperty("name", "AbortError");
    const phase = failedPhase(undefined, false);
    expect(phase).toBe("UNAVAILABLE");
    expect(phase).not.toBe("STOPPED");
  });

  it("shows stale cache immediately on page return and refresh failure", () => {
    expect(initialPhase(true)).toBe("STALE_LAST_SUCCESS");
    expect(failedPhase(500, true)).toBe("STALE_LAST_SUCCESS");
  });

  it("keeps NO_DATA separate from UNAVAILABLE and permission", async () => {
    faults.respond("/empty", { body: { items: [] } });
    const data = await boundedJson<{ items: unknown[] }>("/empty", 100);
    expect(data.items).toHaveLength(0);
    expect("NO_DATA").not.toBe(failedPhase(500, false));
    expect("NO_DATA").not.toBe(failedPhase(403, false));
  });

  it("prevents an older late response from replacing the newest generation", async () => {
    faults.respond("/old", { delayMs: 80, body: { value: "old" } });
    faults.respond("/new", { body: { value: "new" } });
    let generation = 0;
    let visible = "";
    const load = async (path: string) => {
      const requestGeneration = ++generation;
      const response = await boundedJson<{ value: string }>(path, 200);
      if (requestGeneration === generation) visible = response.value;
    };
    const old = load("/old");
    await load("/new");
    await old;
    expect(visible).toBe("new");
  });

  it("keeps collector health separate when the query plane fails", async () => {
    faults.respond("/partial", { body: {
      collector: { status: "RUNNING" },
      query_plane: { status: "UNAVAILABLE" },
    } });
    const data = await boundedJson<{
      collector: { status: string };
      query_plane: { status: string };
    }>("/partial", 100);
    expect(data.collector.status).toBe("RUNNING");
    expect(data.query_plane.status).toBe("UNAVAILABLE");
  });
});
