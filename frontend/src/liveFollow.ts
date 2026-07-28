export type FollowMode = "FOLLOWING_LATEST" | "VIEWING_HISTORY";
export type LogicalRange = { from: number; to: number };

export const RIGHT_EDGE_TOLERANCE_BARS = 1.5;

export function isAtLatestEdge(
  range: LogicalRange | null,
  latestLogicalIndex: number,
  tolerance = RIGHT_EDGE_TOLERANCE_BARS,
) {
  return !!range && latestLogicalIndex - range.to <= tolerance;
}

export function rangeAtLatest(
  current: LogicalRange | null,
  latestLogicalIndex: number,
  fallbackWidth = 259,
): LogicalRange {
  const width = current && current.to > current.from
    ? current.to - current.from
    : fallbackWidth;
  return { from: latestLogicalIndex - width, to: latestLogicalIndex };
}

type FollowState = { mode: FollowMode; hasNewData: boolean };

export class ChartFollowRegistry {
  private states = new Map<string, FollowState>();

  state(key: string): FollowState {
    return this.states.get(key) || { mode: "FOLLOWING_LATEST", hasNewData: false };
  }

  onVisibleRange(key: string, range: LogicalRange | null, latestLogicalIndex: number) {
    const mode: FollowMode = isAtLatestEdge(range, latestLogicalIndex)
      ? "FOLLOWING_LATEST"
      : "VIEWING_HISTORY";
    const next = { mode, hasNewData: mode === "VIEWING_HISTORY" && this.state(key).hasNewData };
    this.states.set(key, next);
    return next;
  }

  onData(key: string, hadNewTimestamp: boolean) {
    const current = this.state(key);
    const next = {
      ...current,
      hasNewData: current.mode === "VIEWING_HISTORY"
        ? current.hasNewData || hadNewTimestamp
        : false,
    };
    this.states.set(key, next);
    return next;
  }

  follow(key: string) {
    const next: FollowState = { mode: "FOLLOWING_LATEST", hasNewData: false };
    this.states.set(key, next);
    return next;
  }
}

export const chartFollowRegistry = new ChartFollowRegistry();

/**
 * Visible-range callbacks are observational. They may change follow state only
 * while a real pointer, touch, or wheel interaction is in progress; chart API
 * calls produce the same callbacks and must never pause the live chart.
 */
export class RangeChangeSource {
  private internalDepth = 0;
  private userActive = false;

  beginInternal() {
    this.internalDepth += 1;
  }

  endInternal() {
    this.internalDepth = Math.max(0, this.internalDepth - 1);
  }

  beginUser() {
    this.userActive = true;
  }

  endUser() {
    this.userActive = false;
  }

  shouldApplyVisibleRange() {
    return this.userActive && this.internalDepth === 0;
  }
}

export function synchronizeLiveViewport<T>(
  timeScale: {
    scrollToRealTime(): void;
    setVisibleRange(range: T): void;
  },
  mode: FollowMode,
  historicalRange: T | null,
) {
  if (mode === "FOLLOWING_LATEST") {
    timeScale.scrollToRealTime();
  } else if (historicalRange) {
    timeScale.setVisibleRange(historicalRange);
  }
}
