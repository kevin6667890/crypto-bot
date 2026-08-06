"""Download and verify OKX official daily trade archives, one file at a time."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen


EXPECTED_COLUMNS = (
    "instrument_name", "trade_id", "side", "price", "size", "created_time"
)
MANIFEST_VERSION = "okx-official-trade-files-manifest-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"ZIP CRC failed for {bad_member}")
        members = archive.infolist()
        if len(members) != 1 or not members[0].filename.endswith(".csv"):
            raise ValueError("expected exactly one CSV member")
        with archive.open(members[0]) as raw:
            reader = csv.reader(io.TextIOWrapper(
                raw, encoding="utf-8-sig", newline=""))
            columns = tuple(next(reader))
        if columns != EXPECTED_COLUMNS:
            raise ValueError(f"unexpected CSV columns: {columns!r}")
        return {
            "member": members[0].filename,
            "uncompressed_size_bytes": members[0].file_size,
            "compressed_member_size_bytes": members[0].compress_size,
            "columns": columns,
            "zip_crc_check": "PASS",
        }


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def files_from_metadata(metadata: dict[str, object]) -> list[dict[str, str]]:
    result = []
    for block in metadata.get("data", []):
        for detail in block.get("details", []):
            for item in detail.get("groupDetails", []):
                result.append({
                    "inst_id": str(detail.get("instId") or ""),
                    "inst_family": str(detail.get("instFamily") or ""),
                    "filename": str(item["filename"]),
                    "url": str(item["url"]),
                    "reported_size_mb": str(item["sizeMB"]),
                    "date_ts": str(item.get("dateTs") or item.get("dataTs")),
                })
    return sorted(result, key=lambda row: (row["date_ts"], row["filename"]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    arguments = parser.parse_args()
    metadata_bytes = arguments.metadata.read_bytes()
    metadata = json.loads(metadata_bytes)
    if str(metadata.get("code")) != "0":
        raise SystemExit("OKX metadata response is not successful")
    arguments.destination.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "manifest_version": MANIFEST_VERSION,
        "metadata_path": str(arguments.metadata.resolve()),
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "created_at_ms": int(time.time() * 1000),
        "files": [],
        "status": "RUNNING",
    }
    existing = {}
    if arguments.manifest.is_file():
        prior = json.loads(arguments.manifest.read_text(encoding="utf-8"))
        if prior.get("metadata_sha256") != payload["metadata_sha256"]:
            raise SystemExit("existing manifest belongs to different metadata")
        existing = {row["filename"]: row for row in prior.get("files", [])}
    rows = files_from_metadata(metadata)
    for position, row in enumerate(rows, 1):
        target = arguments.destination / row["filename"]
        prior = existing.get(row["filename"])
        if (prior and target.is_file()
                and sha256_file(target) == prior.get("sha256")):
            result = prior
            print(json.dumps({"resume": row["filename"], "position": position,
                              "total": len(rows)}), flush=True)
        else:
            partial = target.with_suffix(target.suffix + ".partial")
            digest = hashlib.sha256()
            byte_count = 0
            started = time.monotonic()
            request = Request(row["url"], headers={
                "User-Agent": "crypto-bot-canonical-history-v1"})
            with urlopen(request, timeout=arguments.timeout) as response, partial.open("wb") as output:
                while chunk := response.read(8 * 1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
            verification = verify_archive(partial)
            os.replace(partial, target)
            result = {
                **row,
                **verification,
                "path": str(target.resolve()),
                "size_bytes": byte_count,
                "sha256": digest.hexdigest(),
                "download_duration_seconds": round(time.monotonic() - started, 3),
                "status": "VERIFIED",
            }
            print(json.dumps({"downloaded": row["filename"],
                              "position": position, "total": len(rows),
                              "size_bytes": byte_count}), flush=True)
        existing[row["filename"]] = result
        payload["files"] = [existing[name] for name in sorted(existing)]
        payload["completed_files"] = len(existing)
        payload["expected_files"] = len(rows)
        write_manifest(arguments.manifest, payload)
    payload["status"] = "COMPLETE"
    payload["completed_at_ms"] = int(time.time() * 1000)
    payload["total_size_bytes"] = sum(
        int(row["size_bytes"]) for row in existing.values())
    write_manifest(arguments.manifest, payload)


if __name__ == "__main__":
    main()
