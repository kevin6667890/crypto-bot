"""Download the bounded OKX official 5m OI history into an offline archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


ENDPOINT = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
MANIFEST_VERSION = "okx-official-oi-history-manifest-v1"


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def download(instrument: str, output: Path, max_points: int = 1440) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    end: int | None = None
    rows: dict[int, list[str]] = {}
    pages: list[dict] = []
    while len(rows) < max_points:
        parameters = {"instId": instrument, "period": "5m", "limit": "100"}
        if end is not None:
            parameters["end"] = str(end)
        url = ENDPOINT + "?" + urllib.parse.urlencode(parameters)
        started = time.monotonic()
        request = urllib.request.Request(
            url, headers={"User-Agent": "crypto-bot-canonical-history-v1",
                          "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
        payload = json.loads(body)
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX OI error: {payload.get('code')} {payload.get('msg')}")
        data = payload.get("data", [])
        page_number = len(pages) + 1
        page_path = output / f"{instrument}-page-{page_number:03d}.json"
        page_path.write_bytes(body)
        page_sha = hashlib.sha256(body).hexdigest()
        pages.append({
            "page": page_number, "path": str(page_path), "sha256": page_sha,
            "size_bytes": len(body), "request_url": url,
            "duration_seconds": round(time.monotonic() - started, 3),
            "row_count": len(data),
        })
        if not data:
            break
        prior_count = len(rows)
        for row in data:
            if len(row) != 4:
                raise ValueError(f"unexpected OI row width for {instrument}")
            timestamp = int(row[0])
            fact = [str(value) for value in row]
            previous = rows.get(timestamp)
            if previous is not None and previous != fact:
                raise ValueError(f"conflicting official OI timestamp {timestamp}")
            rows[timestamp] = fact
        if len(rows) == prior_count:
            raise ValueError("OKX OI pagination stopped advancing")
        end = min(int(row[0]) for row in data) - 1
        if len(data) < 100:
            break
        time.sleep(0.25)
    ordered = [rows[key] for key in sorted(rows)]
    rows_path = output / f"{instrument}-oi-5m.json"
    rows_body = canonical_json(ordered)
    rows_path.write_bytes(rows_body)
    return {
        "instrument": instrument, "endpoint": ENDPOINT, "period": "5m",
        "field_order": ["ts", "oi", "oiCcy", "oiUsd"],
        "dedupe_key": "instrument+ts", "page_count": len(pages),
        "row_count": len(ordered), "earliest_ms": int(ordered[0][0]) if ordered else None,
        "latest_ms": int(ordered[-1][0]) if ordered else None,
        "rows_path": str(rows_path),
        "rows_sha256": hashlib.sha256(rows_body).hexdigest(), "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--instrument", action="append", required=True)
    arguments = parser.parse_args()
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at_ms": int(time.time() * 1000), "source": "OKX_OFFICIAL",
        "synthetic_rows": 0, "instruments": [],
    }
    for instrument in arguments.instrument:
        manifest["instruments"].append(download(instrument, arguments.output))
    manifest["completed_at_ms"] = int(time.time() * 1000)
    arguments.manifest.parent.mkdir(parents=True, exist_ok=True)
    arguments.manifest.write_bytes(canonical_json(manifest))


if __name__ == "__main__":
    main()
