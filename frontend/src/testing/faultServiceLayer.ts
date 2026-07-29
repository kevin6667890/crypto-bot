export type MockReply = {
  status?: number;
  delayMs?: number;
  body?: unknown;
};

/** A deterministic fetch-compatible fault layer for page resource tests. */
export class FaultServiceLayer {
  private originalFetch?: typeof fetch;
  private routes = new Map<string, MockReply[]>();

  respond(path: string, ...replies: MockReply[]) {
    this.routes.set(path, [...replies]);
  }

  install() {
    this.originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://fault.test");
      const queue = this.routes.get(url.pathname);
      if (!queue?.length) throw new Error(`Unhandled mock request: ${url.pathname}`);
      const reply = queue.length > 1 ? queue.shift()! : queue[0];
      if (reply.delayMs) {
        await new Promise<void>((resolve, reject) => {
          const timer = setTimeout(resolve, reply.delayMs);
          init?.signal?.addEventListener("abort", () => {
            clearTimeout(timer);
            reject(new DOMException("Aborted", "AbortError"));
          }, { once: true });
        });
      }
      if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return new Response(
        reply.body === undefined ? null : JSON.stringify(reply.body),
        { status: reply.status ?? 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;
  }

  restore() {
    if (this.originalFetch) globalThis.fetch = this.originalFetch;
  }
}

export async function boundedJson<T>(path: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    const response = await fetch(path, { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    return await response.json() as T;
  } finally {
    clearTimeout(timer);
  }
}
