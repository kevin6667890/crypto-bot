"""Sanitized repository leak scanner; never returns matched secret text."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable


PATTERNS = (
    re.compile(r"(?i)(?:deepseek|ai_report)[_ -]?(?:api[_ -]?)?key\s*[:=]\s*['\"]?(?!FILE\b)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._-]{12,}"),
)
TEXT_SUFFIXES = {".py", ".json", ".md", ".yml", ".yaml", ".toml", ".ini", ".env", ".txt", ".js", ".ts", ".tsx"}


def scan_repository(root: str | Path, *, excluded: Iterable[str] = ()) -> dict:
    """Scan source/artifacts only; secret mount paths must be excluded by caller."""
    selected = Path(root).resolve()
    excluded_paths = {Path(item).resolve() for item in excluded}
    findings = []
    scanned = 0
    for path in selected.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if ".git" in path.parts or any(parent == path or parent in path.parents for parent in excluded_paths):
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                findings.append({"path": str(path.relative_to(selected)), "line": line_number})
    return {
        "scanner_version": "ai6b-b3-secret-leak-scanner-v1",
        "scanned_files": scanned,
        "secret_leak_count": len(findings),
        "findings": findings,
        "secret_values_emitted": False,
    }


def secret_presence() -> dict[str, bool]:
    """Presence-only diagnostic: never index or serialize environment values."""
    file_name = os.getenv("AI_REPORT_API_KEY_FILE")
    return {
        "SECRET_PRESENT": bool(file_name and Path(file_name).is_file()),
        "PLAINTEXT_ENV_SECRET_PRESENT": "AI_REPORT_API_KEY" in os.environ,
        "SECRET_IN_PROCESS_ARGUMENTS": any("api-key" in argument.lower() or "bearer" in argument.lower() for argument in sys.argv),
    }
