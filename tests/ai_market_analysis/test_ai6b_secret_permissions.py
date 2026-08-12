from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from scripts.prepare_ai6b_runtime_secrets import (
    SECRET_SPECS,
    SecretPreparationError,
    prepare_runtime_secrets,
)


ROOT = Path(__file__).resolve().parents[2]


def test_secret_layout_is_service_scoped_and_never_world_readable() -> None:
    by_name = {item.name: item for item in SECRET_SPECS}
    assert (by_name["admin_token"].service, by_name["admin_token"].uid) == ("backend", 10001)
    assert (by_name["legacy_deepseek_key"].service, by_name["legacy_deepseek_key"].uid) == (
        "backend",
        10001,
    )
    assert (by_name["ai_report_provider_key"].service, by_name["ai_report_provider_key"].uid) == (
        "report-worker",
        10001,
    )
    assert (by_name["tls_private_key"].service, by_name["tls_private_key"].uid) == ("frontend", 101)
    assert all(item.mode == 0o400 and item.mode & 0o077 == 0 for item in SECRET_SPECS)


def test_prepare_runtime_secrets_does_not_emit_values(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    outputs = tmp_path / "outputs"
    inputs.mkdir()
    values = {item.name: f"private-value-for-{item.name}" for item in SECRET_SPECS}
    for name, value in values.items():
        (inputs / name).write_text(value, encoding="utf-8")
    ownership: list[tuple[str, int, int]] = []

    result = prepare_runtime_secrets(
        inputs,
        outputs,
        owner_setter=lambda path, uid, gid: ownership.append((path.name, uid, gid)),
    )

    rendered = str(result)
    assert result["status"] == "PASS"
    assert result["secret_values_emitted"] is False
    assert all(value not in rendered for value in values.values())
    for item in SECRET_SPECS:
        destination = outputs / item.service / item.name
        assert destination.read_text(encoding="utf-8") == values[item.name]
        if os.name != "nt":
            assert destination.stat().st_mode & 0o777 == 0o400
    assert ownership


def test_prepare_runtime_secrets_rejects_symlink_source(tmp_path: Path) -> None:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    target = tmp_path / "target"
    target.write_text("private", encoding="utf-8")
    try:
        (inputs / SECRET_SPECS[0].name).symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(SecretPreparationError, match="SECRET_SOURCE_NOT_REGULAR"):
        prepare_runtime_secrets(inputs, tmp_path / "outputs", specs=SECRET_SPECS[:1])


def test_compose_secret_mounts_match_authorized_services() -> None:
    compose = yaml.safe_load((ROOT / "deploy/compose/ai6b-production-candidate.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["paper-api"]["secrets"] == ["admin_token", "legacy_deepseek_key"]
    assert services["report-worker"]["secrets"] == ["ai_report_provider_key"]
    assert "secrets" not in services["audit-worker"]
    assert services["frontend"]["secrets"] == ["tls_certificate", "tls_private_key"]
    assert services["paper-api"]["command"] == [
        "python",
        "-m",
        "scripts.run_paper_api_with_secrets",
    ]


def test_read_only_app_services_have_only_bounded_runtime_tmpfs() -> None:
    compose = yaml.safe_load((ROOT / "deploy/compose/ai6b-production-candidate.yml").read_text(encoding="utf-8"))
    tmpfs = compose["x-app-security"]["tmpfs"]
    assert "/tmp:rw,noexec,nosuid,size=64m" in tmpfs
    assert "/app/logs:rw,noexec,nosuid,size=32m" in tmpfs
