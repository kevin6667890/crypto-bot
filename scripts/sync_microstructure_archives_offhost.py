"""Resume-capable verified SSH pull with an explicit off-host ACK."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.microstructure_lifecycle import (  # noqa: E402
    build_offhost_ack,
    canonical_json,
    file_sha256,
    load_raw_trade_manifest,
    verify_raw_trade_archive,
)


def _ssh_base(args: argparse.Namespace) -> list[str]:
    return [
        "ssh",
        "-i", str(args.identity_file),
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "HostKeyAlgorithms=ssh-ed25519",
        "-o", f"UserKnownHostsFile={args.known_hosts}",
        "-o", f"ConnectTimeout={args.timeout}",
        f"{args.user}@{args.host}",
    ]


def _known_fingerprint(args: argparse.Namespace) -> None:
    found = subprocess.run(
        ["ssh-keygen", "-F", args.host, "-f", str(args.known_hosts)],
        check=True,
        capture_output=True,
        text=True,
        timeout=args.timeout,
    ).stdout
    key_lines = "\n".join(
        line for line in found.splitlines() if line and not line.startswith("#")
    )
    fingerprint = subprocess.run(
        ["ssh-keygen", "-lf", "-", "-E", "sha256"],
        input=key_lines,
        check=True,
        capture_output=True,
        text=True,
        timeout=args.timeout,
    ).stdout
    if args.host_fingerprint not in fingerprint or "ED25519" not in fingerprint:
        raise RuntimeError("known_hosts ED25519 fingerprint mismatch")


def _remote_size(args: argparse.Namespace, remote: str) -> int:
    command = _ssh_base(args) + [
        f"stat -c %s -- {shlex.quote(remote)}"
    ]
    return int(
        subprocess.run(
            command, check=True, capture_output=True, text=True,
            timeout=args.timeout,
        ).stdout.strip()
    )


def _pull(args: argparse.Namespace, remote: str, local: Path) -> None:
    expected = _remote_size(args, remote)
    part = local.with_suffix(local.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset > expected:
        raise RuntimeError("partial file is larger than remote source")
    command = _ssh_base(args) + [
        f"tail -c +{offset + 1} -- {shlex.quote(remote)}"
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    started = time.monotonic()
    with part.open("ab") as target:
        assert process.stdout is not None
        while block := process.stdout.read(256 * 1024):
            target.write(block)
            if args.limit_kbps:
                expected_elapsed = target.tell() / (args.limit_kbps * 1024)
                delay = expected_elapsed - (time.monotonic() - started)
                if delay > 0:
                    time.sleep(min(delay, 1))
        target.flush()
        os.fsync(target.fileno())
    if process.wait(timeout=args.timeout) != 0:
        raise RuntimeError(f"SSH transfer failed: {remote}")
    if part.stat().st_size != expected:
        raise RuntimeError(f"SSH transfer size mismatch: {remote}")
    os.replace(part, local)


def _publish_ack(
    args: argparse.Namespace, ack: dict[str, object], remote_name: str
) -> None:
    if not args.server_ack_directory:
        return
    directory = args.server_ack_directory.rstrip("/")
    destination = f"{directory}/{remote_name}"
    temporary = destination + ".tmp"
    command = _ssh_base(args) + [
        "umask 077; "
        f"mkdir -p -- {shlex.quote(directory)}; "
        f"cat > {shlex.quote(temporary)}; "
        f"mv -- {shlex.quote(temporary)} {shlex.quote(destination)}"
    ]
    subprocess.run(
        command,
        input=canonical_json(ack) + "\n",
        check=True,
        text=True,
        timeout=args.timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--host-fingerprint", required=True)
    parser.add_argument("--remote-archive", required=True)
    parser.add_argument("--remote-manifest", required=True)
    parser.add_argument("--local-directory", type=Path, required=True)
    parser.add_argument("--server-ack-directory")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit-kbps", type=int)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()
    _known_fingerprint(args)
    args.local_directory.mkdir(parents=True, exist_ok=True)
    archive = args.local_directory / Path(args.remote_archive).name
    manifest_path = args.local_directory / Path(args.remote_manifest).name
    if args.dry_run:
        print(json.dumps(
            {
                "dry_run": True,
                "remote_archive": args.remote_archive,
                "remote_manifest": args.remote_manifest,
                "local_directory": str(args.local_directory),
            },
            sort_keys=True,
        ))
        return 0
    for remote, local in (
        (args.remote_manifest, manifest_path),
        (args.remote_archive, archive),
    ):
        for attempt in range(1, args.retries + 1):
            try:
                if local.exists() and local.stat().st_size == _remote_size(args, remote):
                    break
                _pull(args, remote, local)
                break
            except (OSError, RuntimeError, subprocess.SubprocessError):
                if attempt == args.retries:
                    raise
                time.sleep(min(2 ** attempt, 10))
    manifest = load_raw_trade_manifest(manifest_path)
    verification = verify_raw_trade_archive(archive, manifest_path)
    manifest["verification"] = verification
    if file_sha256(archive) != manifest["archive_sha256"]:
        raise RuntimeError("downloaded archive hash mismatch")
    ack = build_offhost_ack(
        manifest,
        local_verification_time=datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
    )
    ack_path = archive.with_suffix(archive.suffix + ".complete.ack.json")
    temporary = ack_path.with_suffix(ack_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(ack, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, ack_path)
    _publish_ack(args, ack, ack_path.name)
    print(json.dumps(
        {
            "archive": str(archive),
            "manifest": str(manifest_path),
            "ack": str(ack_path),
            "verification": verification,
        },
        sort_keys=True,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
