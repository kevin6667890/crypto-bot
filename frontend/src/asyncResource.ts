import { useCallback, useEffect, useRef, useState } from "react";

export type AsyncPhase =
  | "LOADING"
  | "READY"
  | "STALE_LAST_SUCCESS"
  | "UNAVAILABLE"
  | "NO_DATA"
  | "PERMISSION_REQUIRED";

export type AsyncResource<T> = {
  phase: AsyncPhase;
  data?: T;
  dataAsOf?: string;
  errorType?: string;
  refreshing: boolean;
  refresh: () => void;
};

type CachedValue = { data: unknown; dataAsOf?: string };
const cache = new Map<string, CachedValue>();

export function clearAsyncResourceCacheForTests() {
  cache.clear();
}

export function initialPhase(hasCachedValue: boolean): AsyncPhase {
  return hasCachedValue ? "STALE_LAST_SUCCESS" : "LOADING";
}

export function failedPhase(
  status: number | undefined,
  hasCachedValue: boolean,
): AsyncPhase {
  if (status === 401 || status === 403) return "PERMISSION_REQUIRED";
  return hasCachedValue ? "STALE_LAST_SUCCESS" : "UNAVAILABLE";
}

function snapshotTime(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const body = value as Record<string, unknown>;
  const snapshot = body._snapshot;
  if (snapshot && typeof snapshot === "object") {
    const dataAsOf = (snapshot as Record<string, unknown>).data_as_of;
    if (typeof dataAsOf === "string") return dataAsOf;
  }
  return typeof body.generated_at === "string" ? body.generated_at : undefined;
}

export function useAsyncResource<T>(
  key: string,
  url: string,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    isNoData?: (value: T) => boolean;
  } = {},
): AsyncResource<T> {
  const retained = cache.get(key) as CachedValue | undefined;
  const [state, setState] = useState<Omit<AsyncResource<T>, "refresh">>({
    phase: initialPhase(Boolean(retained)),
    data: retained?.data as T | undefined,
    dataAsOf: retained?.dataAsOf,
    refreshing: true,
  });
  const generation = useRef(0);
  const controller = useRef<AbortController>();
  const [refreshToken, setRefreshToken] = useState(0);
  const refresh = useCallback(() => setRefreshToken((value) => value + 1), []);

  useEffect(() => {
    const requestGeneration = ++generation.current;
    controller.current?.abort();
    const active = new AbortController();
    controller.current = active;
    const retainedValue = cache.get(key) as CachedValue | undefined;
    setState((current) => ({
      ...current,
      phase: initialPhase(Boolean(retainedValue)),
      data: (retainedValue?.data as T | undefined) ?? current.data,
      dataAsOf: retainedValue?.dataAsOf ?? current.dataAsOf,
      refreshing: true,
      errorType: undefined,
    }));

    const run = async () => {
      let lastError: unknown;
      let lastStatus: number | undefined;
      for (let attempt = 0; attempt < 2; attempt += 1) {
        const timeout = window.setTimeout(
          () => active.abort("timeout"),
          options.timeoutMs ?? 8_000,
        );
        try {
          const response = await fetch(url, {
            signal: active.signal,
            cache: "no-store",
          });
          lastStatus = response.status;
          if (!response.ok) throw new Error(`HTTP_${response.status}`);
          const data = (await response.json()) as T;
          if (requestGeneration !== generation.current || active.signal.aborted)
            return;
          const dataAsOf = snapshotTime(data);
          if (options.isNoData?.(data)) {
            setState({
              phase: "NO_DATA",
              data,
              dataAsOf,
              refreshing: false,
            });
            return;
          }
          cache.set(key, { data, dataAsOf });
          setState({
            phase: "READY",
            data,
            dataAsOf,
            refreshing: false,
          });
          return;
        } catch (error) {
          lastError = error;
          if (active.signal.aborted) break;
          if (attempt === 0) {
            await new Promise((resolve) => window.setTimeout(resolve, 500));
          }
        } finally {
          window.clearTimeout(timeout);
        }
      }
      if (requestGeneration !== generation.current) return;
      const cached = cache.get(key) as CachedValue | undefined;
      setState({
        phase: failedPhase(lastStatus, Boolean(cached)),
        data: cached?.data as T | undefined,
        dataAsOf: cached?.dataAsOf,
        errorType:
          active.signal.reason === "timeout"
            ? "TIMEOUT"
            : lastStatus
              ? `HTTP_${lastStatus}`
              : lastError instanceof Error
                ? lastError.name
                : "NETWORK_ERROR",
        refreshing: false,
      });
    };
    void run();
    return () => active.abort("superseded");
  }, [key, url, refreshToken, options.timeoutMs, options.isNoData]);

  useEffect(() => {
    if (!options.intervalMs) return undefined;
    const timer = window.setInterval(refresh, options.intervalMs);
    return () => window.clearInterval(timer);
  }, [options.intervalMs, refresh]);

  return { ...state, refresh };
}
