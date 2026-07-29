"""Bounded read-only latency probe for production query endpoints."""
from __future__ import annotations

import argparse
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINTS = (
    "/api/research/microstructure/health",
    "/api/research/microstructure/coverage",
    "/api/research/microstructure/eligibility",
    "/api/research/microstructure/gaps",
    "/api/research/microstructure/validation",
    "/api/operations/summary",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    args = parser.parse_args()
    for endpoint in ENDPOINTS:
        started = time.monotonic()
        try:
            with urlopen(
                Request(f"{args.base_url.rstrip('/')}{endpoint}"),
                timeout=10,
            ) as response:
                body = response.read()
                payload = json.loads(body)
                print(json.dumps({
                    "endpoint": endpoint,
                    "status": response.status,
                    "total_ms": round((time.monotonic() - started) * 1000, 3),
                    "bytes": len(body),
                    "server_timing": response.headers.get("Server-Timing"),
                    "query_profile": payload.get("_query_profile"),
                    "snapshot": payload.get("_snapshot"),
                    "keys": list(payload)[:12],
                }, sort_keys=True))
        except (HTTPError, URLError, TimeoutError) as error:
            print(json.dumps({
                "endpoint": endpoint,
                "total_ms": round((time.monotonic() - started) * 1000, 3),
                "error": f"{type(error).__name__}: {error}",
            }, sort_keys=True))


if __name__ == "__main__":
    main()
