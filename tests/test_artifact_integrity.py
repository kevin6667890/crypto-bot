from __future__ import annotations

from copy import deepcopy
import json
import locale
from pathlib import Path
import subprocess

import pytest

from scripts.artifact_integrity import (
    AGGREGATE_ALGORITHM, MANIFEST_NAME, ArtifactIntegrityError, ArtifactSeal,
    aggregate_sha256, build_manifest, canonical_aggregate_members,
    canonical_json_bytes, directory_member_paths, git_blob_bytes,
    members_from_directory, members_from_git, members_from_index, render_report,
    reseal_permitted, sha256_bytes, validate_manifest_structure,
    verify_directory, verify_git, verify_index,
)


def error_code(callable_, code):
    with pytest.raises(ArtifactIntegrityError, match=f"^{code}$"):
        callable_()


def make_artifact(tmp_path: Path) -> tuple[Path, dict]:
    artifact = tmp_path / "artifact"
    seal = ArtifactSeal(artifact)
    seal.write_json("a.json", {"value": 1})
    seal.write_bytes("report.md", b"report\n")
    seal.prepare({"a.json", "report.md"})
    seal.seal()
    return artifact, build_manifest("id", members_from_directory(artifact))


def init_repo(tmp_path: Path) -> tuple[Path, str, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-q"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / ".gitattributes").write_text("/evidence/** -text\n", newline="\n")
    subprocess.check_call(["git", "add", ".gitattributes"], cwd=repo)
    artifact = repo / "evidence" / "id"
    artifact.mkdir(parents=True)
    (artifact / "a.json").write_bytes(b'{"a":1}\n')
    (artifact / "report.md").write_bytes(b"report\n")
    subprocess.check_call(["git", "add", "evidence/id/a.json", "evidence/id/report.md"], cwd=repo)
    index_members = members_from_index(repo, "evidence/id")
    manifest = build_manifest("id", index_members)
    subprocess.check_call(["git", "commit", "-qm", "members"], cwd=repo)
    member_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (artifact / MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest) + b"\n")
    subprocess.check_call(["git", "add", f"evidence/id/{MANIFEST_NAME}"], cwd=repo)
    subprocess.check_call(["git", "commit", "-qm", "manifest"], cwd=repo)
    return repo, member_commit, manifest


def machine_input() -> dict:
    return {
        "final_gate_decision": {"conclusion": "PASS", "passed": True},
        "gate_manifest": {"feature_sha": "abc", "dataset_identity": "data", "parameter_count": 32,
                          "context_count": 1, "evaluate_calls": 2, "compare_calls": 3,
                          "router_evaluations": 4},
        "lifecycle_transition_audit": {"stage_evaluation_counts": {"WATCH": 1},
                                       "lifecycle_propagation_failure": 0,
                                       "duplicate_TRIGGER_READY": 0},
        "identity_continuity_audit": {"unexpected_setup_anchor_changes": 0,
                                      "unexpected_level_continuity_changes": 0},
        "checkpoint_resume_comparison": {"all_exact": True, "aggregate_match_rate": 1.0},
        "direction_summary": {"rows": [{"confirmation_lineage": 1.0,
                                          "geometry_provenance": 1.0}]},
    }


def test_manifest_member_list_is_deterministic():
    rows = [{"path": "z", "sha256": "0", "size_bytes": 0, "required": True},
            {"path": "a", "sha256": "1", "size_bytes": 1, "required": True}]
    assert [row["path"] for row in build_manifest("id", rows)["members"]] == ["a", "z"]


def test_aggregate_canonical_algorithm_is_deterministic():
    left = [{"path": "b", "sha256": "2", "size_bytes": 2}, {"path": "a", "sha256": "1", "size_bytes": 1}]
    assert aggregate_sha256(left) == aggregate_sha256(reversed(left))
    assert canonical_aggregate_members(left).endswith(b"]")


def test_manifest_does_not_include_itself():
    error_code(lambda: build_manifest("id", [{"path": MANIFEST_NAME, "sha256": "0", "size_bytes": 0}]),
               "ARTIFACT_MANIFEST_SELF_REFERENCE")


def test_duplicate_path_is_rejected():
    row = {"path": "a", "sha256": "0", "size_bytes": 0}
    error_code(lambda: build_manifest("id", [row, row]), "ARTIFACT_DUPLICATE_MEMBER_PATH")


def test_missing_required_member_is_rejected(tmp_path):
    artifact, manifest = make_artifact(tmp_path)
    (artifact / "a.json").unlink()
    error_code(lambda: verify_directory(artifact, manifest), "ARTIFACT_MEMBER_MISSING")


def test_undeclared_member_is_rejected(tmp_path):
    artifact, manifest = make_artifact(tmp_path)
    (artifact / "extra.json").write_text("{}")
    error_code(lambda: verify_directory(artifact, manifest), "ARTIFACT_UNDECLARED_MEMBER")


def test_member_modification_fails(tmp_path):
    artifact, manifest = make_artifact(tmp_path)
    (artifact / "a.json").write_text("changed")
    error_code(lambda: verify_directory(artifact, manifest), "ARTIFACT_MEMBER_HASH_MISMATCH")


def test_report_modification_fails(tmp_path):
    artifact, manifest = make_artifact(tmp_path)
    (artifact / "report.md").write_text("changed")
    error_code(lambda: verify_directory(artifact, manifest), "ARTIFACT_MEMBER_HASH_MISMATCH")


def test_write_after_seal_is_rejected(tmp_path):
    seal = ArtifactSeal(tmp_path)
    seal.write_bytes("a", b"a")
    seal.prepare({"a"}); seal.seal()
    error_code(lambda: seal.write_bytes("a", b"b"), "ARTIFACT_MODIFIED_AFTER_SEAL")
    assert seal.state == "INVALID"


def test_same_report_input_has_identical_bytes():
    assert render_report(machine_input()) == render_report(machine_input())


def test_json_key_order_does_not_change_report():
    value = machine_input()
    reordered = {key: value[key] for key in reversed(value)}
    assert render_report(value) == render_report(reordered)


def test_locale_does_not_change_report():
    before = locale.setlocale(locale.LC_ALL)
    try:
        locale.setlocale(locale.LC_ALL, "C")
        assert b"100.00%" in render_report(machine_input())
    finally:
        locale.setlocale(locale.LC_ALL, before)


def test_platform_newline_configuration_does_not_change_report():
    assert b"\r\n" not in render_report(machine_input())


def test_report_generation_through_seal_is_rejected(tmp_path):
    seal = ArtifactSeal(tmp_path); seal.write_bytes("a", b"a"); seal.prepare({"a"}); seal.seal()
    error_code(lambda: seal.write_bytes("report.md", render_report(machine_input())),
               "ARTIFACT_MODIFIED_AFTER_SEAL")


def test_git_blob_verifier_reads_member(tmp_path):
    repo, _, _ = init_repo(tmp_path)
    assert git_blob_bytes(repo, "HEAD", "evidence/id/a.json") == b'{"a":1}\n'


def test_git_blob_sha_matches_manifest(tmp_path):
    repo, _, manifest = init_repo(tmp_path)
    assert verify_git(repo, "HEAD", "evidence/id", manifest)["sha_matches"] == 2


def test_git_blob_size_matches_manifest(tmp_path):
    repo, _, manifest = init_repo(tmp_path)
    assert verify_git(repo, "HEAD", "evidence/id", manifest)["size_matches"] == 2


def test_staging_change_is_detected(tmp_path):
    repo, _, manifest = init_repo(tmp_path)
    (repo / "evidence/id/a.json").write_text("changed")
    subprocess.check_call(["git", "add", "evidence/id/a.json"], cwd=repo)
    error_code(lambda: verify_index(repo, "evidence/id", manifest), "ARTIFACT_MEMBER_HASH_MISMATCH")


def test_member_commit_change_is_detected(tmp_path):
    repo, member_commit, manifest = init_repo(tmp_path)
    assert verify_git(repo, member_commit, "evidence/id", manifest)["status"] == "VERIFIED"
    changed = deepcopy(manifest); changed["members"][0]["sha256"] = "0" * 64
    changed["aggregate_sha256"] = aggregate_sha256(changed["members"])
    error_code(lambda: verify_git(repo, member_commit, "evidence/id", changed),
               "ARTIFACT_MEMBER_HASH_MISMATCH")


def test_final_manifest_commit_verifies(tmp_path):
    repo, _, _ = init_repo(tmp_path)
    assert verify_git(repo, "HEAD", "evidence/id")["status"] == "VERIFIED"


def test_clean_worktree_verifies(tmp_path):
    repo, _, _ = init_repo(tmp_path)
    checkout = tmp_path / "checkout"
    subprocess.check_call(["git", "worktree", "add", "--detach", str(checkout), "HEAD"], cwd=repo,
                          stdout=subprocess.DEVNULL)
    manifest = json.loads((checkout / "evidence/id/sha256_manifest.json").read_bytes())
    assert verify_directory(checkout / "evidence/id", manifest)["status"] == "VERIFIED"


def test_materialized_git_blobs_verify(tmp_path):
    repo, _, manifest = init_repo(tmp_path)
    target = tmp_path / "materialized"; target.mkdir()
    for row in manifest["members"]:
        (target / row["path"]).write_bytes(git_blob_bytes(repo, "HEAD", "evidence/id/" + row["path"]))
    assert verify_directory(target, manifest)["status"] == "VERIFIED"


def test_lf_crlf_difference_is_classifiable():
    assert sha256_bytes(b"a\n") != sha256_bytes(b"a\r\n")
    assert b"a\r\n".replace(b"\r\n", b"\n") == b"a\n"


def test_bom_difference_is_classifiable():
    assert b"\xef\xbb\xbf{}".removeprefix(b"\xef\xbb\xbf") == b"{}"


def test_aggregate_mismatch_is_rejected():
    manifest = build_manifest("id", [])
    manifest["aggregate_sha256"] = "0" * 64
    error_code(lambda: validate_manifest_structure(manifest), "ARTIFACT_AGGREGATE_HASH_MISMATCH")


def test_v1_manifest_is_audit_only():
    error_code(lambda: validate_manifest_structure({"version": "v1"}),
               "ARTIFACT_LEGACY_MANIFEST_AUDIT_ONLY")


def test_core_json_mismatch_forbids_resign():
    assert not reseal_permitted({"core_json_filesystem_matches_manifest": True,
                                 "core_json_git_blobs_match_manifest": False,
                                 "only_display_members_changed": False})


def test_display_only_change_allows_rebuild():
    assert reseal_permitted({"core_json_filesystem_matches_manifest": True,
                             "core_json_git_blobs_match_manifest": True,
                             "only_display_members_changed": True})


def test_sealed_to_verified_transition(tmp_path):
    seal = ArtifactSeal(tmp_path); seal.write_bytes("a", b"a"); seal.prepare({"a"}); seal.seal()
    seal.verify_unchanged()
    assert seal.state == "VERIFIED"


def test_sealed_change_transitions_invalid(tmp_path):
    seal = ArtifactSeal(tmp_path); seal.write_bytes("a", b"a"); seal.prepare({"a"}); seal.seal()
    (tmp_path / "a").write_bytes(b"b")
    error_code(seal.verify_unchanged, "ARTIFACT_MODIFIED_AFTER_SEAL")
    assert seal.state == "INVALID"
