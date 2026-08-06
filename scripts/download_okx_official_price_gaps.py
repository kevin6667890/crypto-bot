#!/usr/bin/env python3
"""Download verified OKX mark/index 1m candles for audited raw gaps."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path


MANIFEST_VERSION = "okx-official-price-gap-manifest-v1"
ENDPOINTS = {
    "mark": "https://www.okx.com/api/v5/market/history-mark-price-candles",
    "index": "https://www.okx.com/api/v5/market/history-index-candles",
}


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def request_page(url: str) -> tuple[bytes, int]:
    for attempt in range(7):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "crypto-bot-canonical-history-v1",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read(), attempt
        except (TimeoutError, URLError, OSError, HTTPError) as error:
            retryable = not isinstance(error, HTTPError) or error.code in {429, 500, 502, 503, 504}
            if not retryable or attempt == 6:
                raise
            time.sleep(min(2 ** attempt, 12))
    raise RuntimeError("OKX price request exhausted retry budget")


def download_source(
    source: str, instrument: str, gaps: list[dict], output: Path,
) -> dict:
    api_instrument = instrument.removesuffix("-SWAP") if source == "index" else instrument
    endpoint = ENDPOINTS[source]
    points: dict[int, list[str]] = {}
    pages: list[dict] = []
    gap_results: list[dict] = []
    delay = 0.22 if source == "index" else 0.12
    for gap_number, gap in enumerate(gaps, 1):
        original_start = int(gap["start_ms"])
        original_end = int(gap["end_ms_exclusive"])
        request_start = original_start - 120_000
        request_end = original_end + 120_000
        cursor = request_end
        prior_oldest: int | None = None
        while cursor > request_start:
            parameters = {
                "instId": api_instrument, "bar": "1m", "limit": "100",
                "after": str(cursor),
            }
            url = endpoint + "?" + urllib.parse.urlencode(parameters)
            started = time.monotonic()
            body, retries = request_page(url)
            payload = json.loads(body)
            if payload.get("code") != "0":
                raise RuntimeError(
                    f"OKX {source} error: {payload.get('code')} {payload.get('msg')}"
                )
            data = payload.get("data", [])
            page_number = len(pages) + 1
            page_path = output / (
                f"{source}-{instrument}-gap-{gap_number:03d}-page-{page_number:04d}.json"
            )
            page_path.write_bytes(body)
            pages.append({
                "gap_number": gap_number, "page": page_number,
                "path": str(page_path), "request_url": url,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size_bytes": len(body), "row_count": len(data),
                "duration_seconds": round(time.monotonic() - started, 3),
                "retries": retries,
            })
            if not data:
                break
            timestamps: list[int] = []
            for raw in data:
                if len(raw) != 6:
                    raise ValueError(f"unexpected {source} candle width")
                timestamp = int(raw[0])
                timestamps.append(timestamp)
                if raw[5] != "1" or not request_start <= timestamp < request_end:
                    continue
                row = [str(value) for value in raw]
                previous = points.get(timestamp)
                if previous is not None and previous != row:
                    raise ValueError(f"conflicting {source} candle {instrument} {timestamp}")
                points[timestamp] = row
            oldest = min(timestamps)
            if prior_oldest is not None and oldest >= prior_oldest:
                raise RuntimeError(f"OKX {source} pagination did not advance")
            prior_oldest = oldest
            if oldest <= request_start or len(data) < 100:
                break
            cursor = oldest
            time.sleep(delay)
        expected = set(range(original_start, original_end, 60_000))
        recovered = sorted(expected.intersection(points))
        gap_results.append({
            "start_ms": original_start, "end_ms_exclusive": original_end,
            "expected_minutes": len(expected), "recovered_minutes": len(recovered),
            "unavailable_minutes": len(expected) - len(recovered),
            "status": "COMPLETE" if len(recovered) == len(expected) else "PARTIAL",
        })

    ordered = [points[key] for key in sorted(points)]
    rows_path = output / f"{source}-{instrument}-1m.json"
    rows_body = canonical_json(ordered)
    rows_path.write_bytes(rows_body)
    return {
        "source": source, "instrument": instrument,
        "api_instrument": api_instrument, "endpoint": endpoint,
        "field_order": ["ts", "open", "high", "low", "close", "confirm"],
        "dedupe_key": "source+instrument+ts+resolution",
        "requested_gap_count": len(gaps),
        "requested_gap_minutes": sum(int(gap["minutes"]) for gap in gaps),
        "row_count_with_overlap": len(ordered),
        "earliest_ms": int(ordered[0][0]) if ordered else None,
        "latest_ms": int(ordered[-1][0]) if ordered else None,
        "rows_path": str(rows_path),
        "rows_sha256": hashlib.sha256(rows_body).hexdigest(),
        "page_count": len(pages), "pages": pages, "gaps": gap_results,
        "status": "COMPLETE" if all(
            item["status"] == "COMPLETE" for item in gap_results
        ) else "PARTIAL",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.coverage_report.read_text(encoding="utf-8"))
    coverage_sha256 = hashlib.sha256(args.coverage_report.read_bytes()).hexdigest()
    args.output.mkdir(parents=True, exist_ok=True)
    progress_path = args.manifest.with_suffix(args.manifest.suffix + ".progress")
    completed: dict[tuple[str, str], dict] = {}
    if progress_path.exists():
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if progress.get("coverage_sha256") == coverage_sha256:
            for item in progress.get("instruments", []):
                rows_path = Path(item["rows_path"])
                if (rows_path.exists()
                        and hashlib.sha256(rows_path.read_bytes()).hexdigest()
                        == item["rows_sha256"]):
                    completed[(item["source"], item["instrument"])] = item
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at_ms": int(time.time() * 1000), "source": "OKX_OFFICIAL",
        "synthetic_rows": 0, "interpolated_rows": 0,
        "coverage_sha256": coverage_sha256, "instruments": [],
    }
    for source in ENDPOINTS:
        for instrument in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            gaps = [
                item for item in report["gaps"]
                if item["source"] == source and item["instrument"] == instrument
                and item["classification"] == "TRUE_RAW_GAP"
            ]
            item = completed.get((source, instrument))
            if item is None:
                item = download_source(source, instrument, gaps, args.output)
            manifest["instruments"].append(item)
            progress_path.write_bytes(canonical_json(manifest))
    manifest["completed_at_ms"] = int(time.time() * 1000)
    manifest["status"] = "COMPLETE" if all(
        item["status"] == "COMPLETE" for item in manifest["instruments"]
    ) else "PARTIAL"
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_bytes(canonical_json(manifest))


if __name__ == "__main__":
    main()
