export type UnixSeconds = number & { readonly __unit: "unix-seconds" };
export type UnixMilliseconds = number & { readonly __unit: "unix-milliseconds" };

export function asUnixSeconds(value: number): UnixSeconds {
  if (!Number.isInteger(value) || value < 0 || value >= 10_000_000_000) {
    throw new RangeError("Expected a non-negative Unix timestamp in seconds");
  }
  return value as UnixSeconds;
}

export function asUnixMilliseconds(value: number): UnixMilliseconds {
  if (!Number.isInteger(value) || value < 10_000_000_000) {
    throw new RangeError("Expected a Unix timestamp in milliseconds");
  }
  return value as UnixMilliseconds;
}

export function secondsToMilliseconds(value: UnixSeconds): UnixMilliseconds {
  return asUnixMilliseconds(value * 1000);
}

export function millisecondsToSeconds(value: UnixMilliseconds): UnixSeconds {
  return asUnixSeconds(Math.floor(value / 1000));
}
