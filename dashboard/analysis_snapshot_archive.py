"""Verified reads for content-addressed analysis snapshot archives."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ARCHIVE_STUB_VERSION = "analysis-snapshot-archive-stub-v1"


def payload_sha256(payload: str | bytes) -> str:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(raw).hexdigest()


def archive_stub(
    *, uri: str, digest: str, codec: str, original_size: int
) -> str:
    return json.dumps(
        {
            "_archive": {
                "version": ARCHIVE_STUB_VERSION,
                "uri": uri,
                "sha256": digest,
                "codec": codec,
                "original_size": original_size,
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def stub_metadata(payload: str) -> Mapping[str, Any] | None:
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None
    metadata = value.get("_archive") if isinstance(value, dict) else None
    if (
        isinstance(metadata, dict)
        and metadata.get("version") == ARCHIVE_STUB_VERSION
        and isinstance(metadata.get("uri"), str)
        and isinstance(metadata.get("sha256"), str)
    ):
        return metadata
    return None


def read_archived_payload(payload: str, archive_directory: Path | str) -> str:
    """Return inline payloads unchanged and verify archived payloads on read."""
    metadata = stub_metadata(payload)
    if metadata is None:
        return payload
    root = Path(archive_directory).resolve()
    blob = (root / metadata["uri"]).resolve()
    try:
        blob.relative_to(root)
    except ValueError as error:
        raise ValueError("archive URI escapes archive directory") from error
    codec = metadata.get("codec")
    if codec == "gzip":
        raw = gzip.decompress(blob.read_bytes())
    elif codec == "none":
        raw = blob.read_bytes()
    else:
        raise ValueError(f"unsupported snapshot archive codec: {codec}")
    if len(raw) != int(metadata.get("original_size", -1)):
        raise ValueError("archived snapshot size mismatch")
    if payload_sha256(raw) != metadata["sha256"]:
        raise ValueError("archived snapshot SHA-256 mismatch")
    return raw.decode("utf-8")
