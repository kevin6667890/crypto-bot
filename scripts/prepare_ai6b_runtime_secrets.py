"""Prepare least-privilege file secrets for local Docker Compose bind mounts."""
from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class SecretSpec:
    name: str
    service: str
    uid: int
    gid: int
    mode: int = 0o400


SECRET_SPECS = (
    SecretSpec("admin_token", "backend", 10001, 10001),
    SecretSpec("legacy_deepseek_key", "backend", 10001, 10001),
    SecretSpec("ai_report_provider_key", "report-worker", 10001, 10001),
    SecretSpec("tls_certificate", "frontend", 101, 101),
    SecretSpec("tls_private_key", "frontend", 101, 101),
)


class SecretPreparationError(RuntimeError):
    """Fail-closed error raised before an unsafe secret layout is created."""


def _regular_source(path: Path) -> None:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError as error:
        raise SecretPreparationError(f"MISSING_SECRET_SOURCE:{path.name}") from error
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise SecretPreparationError(f"SECRET_SOURCE_NOT_REGULAR:{path.name}")
    if info.st_size == 0:
        raise SecretPreparationError(f"EMPTY_SECRET_SOURCE:{path.name}")


def _copy_atomic(
    source: Path,
    destination: Path,
    spec: SecretSpec,
    owner_setter: Callable[[Path, int, int], None],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    owner_setter(destination.parent, spec.uid, spec.gid)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{spec.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(temporary, spec.mode)
        owner_setter(temporary, spec.uid, spec.gid)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_runtime_secrets(
    input_directory: Path,
    output_directory: Path,
    specs: Iterable[SecretSpec] = SECRET_SPECS,
    owner_setter: Callable[[Path, int, int], None] | None = None,
) -> dict[str, object]:
    input_root = input_directory.resolve(strict=True)
    output_root = output_directory.resolve(strict=False)
    if input_root == output_root or input_root in output_root.parents or output_root in input_root.parents:
        raise SecretPreparationError("OUTPUT_DIRECTORY_MUST_NOT_OVERLAP_INPUT")
    if owner_setter is None:
        owner_setter = getattr(os, "chown", None)
        if owner_setter is None:
            raise SecretPreparationError("NUMERIC_OWNER_OPERATION_UNAVAILABLE")
    output_root.mkdir(parents=True, exist_ok=True)
    os.chmod(output_root, 0o700)

    layout: list[dict[str, object]] = []
    for spec in specs:
        source = input_root / spec.name
        _regular_source(source)
        destination = output_root / spec.service / spec.name
        _copy_atomic(source, destination, spec, owner_setter)
        layout.append(
            {
                "name": spec.name,
                "service": spec.service,
                "uid": spec.uid,
                "gid": spec.gid,
                "mode": format(spec.mode, "04o"),
            }
        )
    return {"status": "PASS", "secret_values_emitted": False, "layout": layout}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare non-root AI6B Compose secrets without exposing their values"
    )
    parser.add_argument("--input-directory", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        parser.error("ROOT_REQUIRED_TO_SET_NUMERIC_SECRET_OWNERS")
    result = prepare_runtime_secrets(arguments.input_directory, arguments.output_directory)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
