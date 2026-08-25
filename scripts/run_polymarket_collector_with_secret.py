"""Start the one-shot Polymarket collector with a file-backed provider secret.

The secret is read inside the container so it is not present in the Compose
environment, image metadata, or the web/API process.
"""
from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    secret_path = os.environ.get("POLYMARKET_LLM_API_KEY_FILE", "").strip()
    if not secret_path:
        raise RuntimeError("POLYMARKET_LLM_API_KEY_FILE_UNSET")
    value = Path(secret_path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("POLYMARKET_LLM_API_KEY_FILE_EMPTY")

    os.environ["POLYMARKET_LLM_API_KEY"] = value
    try:
        from dashboard.polymarket.__main__ import main as collector_main

        return collector_main()
    finally:
        os.environ.pop("POLYMARKET_LLM_API_KEY", None)


if __name__ == "__main__":
    raise SystemExit(main())
