"""Fail-closed guard for the one authorized production deployment origin."""
from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "deploy" / "production_origin.env"
REQUIRED = ("PRODUCTION_ORIGIN_HOST", "PRODUCTION_ORIGIN_USER", "PRODUCTION_ORIGIN_APP_DIR")


def load_origin(path: Path = CONFIG) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise ValueError("PRODUCTION_ORIGIN_CONFIG_INVALID")
        values[key] = value
    if any(not values.get(key) for key in REQUIRED):
        raise ValueError("PRODUCTION_ORIGIN_CONFIG_UNSET")
    if values["PRODUCTION_ORIGIN_HOST"] != "8.217.62.226":
        raise ValueError("PRODUCTION_ORIGIN_NOT_CANONICAL")
    if values["PRODUCTION_ORIGIN_USER"] != "root" or values["PRODUCTION_ORIGIN_APP_DIR"] != "/opt/crypto-bot":
        raise ValueError("PRODUCTION_ORIGIN_IDENTITY_INVALID")
    return values


def assert_target(target: str | None, path: Path = CONFIG) -> dict[str, str]:
    values = load_origin(path)
    if not target:
        raise ValueError("PRODUCTION_TARGET_UNSET")
    if target != values["PRODUCTION_ORIGIN_HOST"]:
        raise ValueError("PRODUCTION_TARGET_BLOCKED")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the only production deployment target.")
    parser.add_argument("--target", required=True)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    values = assert_target(args.target, args.config)
    print(f"PRODUCTION_ORIGIN_OK {values['PRODUCTION_ORIGIN_USER']}@{values['PRODUCTION_ORIGIN_HOST']} {values['PRODUCTION_ORIGIN_APP_DIR']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
