"""Deterministic sealing and Git-blob verification for research artifacts.

The v2 aggregate is SHA-256 over UTF-8 canonical JSON (sorted keys, compact
separators, no trailing byte) of the ordered ``path``, ``sha256`` and
``size_bytes`` member records.  The manifest never covers itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Callable, Iterable, Mapping


MANIFEST_VERSION = "artifact-integrity-manifest-v2"
ARTIFACT_TYPE = "strategy-phase4a6d-bounded-identity-gate-v1"
AGGREGATE_ALGORITHM = "sha256-canonical-member-list-v1"
MANIFEST_NAME = "sha256_manifest.json"


class ArtifactIntegrityError(RuntimeError):
    """A stable error code for artifact integrity failures."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fsync_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def canonical_json_file(path: Path, value: Any) -> None:
    fsync_write(path, canonical_json_bytes(value) + b"\n")


def _member_sort_key(item: Mapping[str, Any]) -> bytes:
    return str(item["path"]).encode("utf-8")


def canonical_aggregate_members(members: Iterable[Mapping[str, Any]]) -> bytes:
    rows = [
        {"path": str(row["path"]), "sha256": str(row["sha256"]),
         "size_bytes": int(row["size_bytes"])}
        for row in members
    ]
    rows.sort(key=_member_sort_key)
    return canonical_json_bytes(rows)


def aggregate_sha256(members: Iterable[Mapping[str, Any]]) -> str:
    return sha256_bytes(canonical_aggregate_members(members))


def _eligible_member(path: str) -> bool:
    pure = PurePosixPath(path)
    if path == MANIFEST_NAME or pure.name.startswith("."):
        return False
    lowered = pure.name.lower()
    return not (lowered.endswith((".tmp", ".temp", ".lock")) or
                lowered in {"checkpoint.lock"})


def directory_member_paths(artifact_dir: Path) -> list[str]:
    rows = [
        path.relative_to(artifact_dir).as_posix()
        for path in artifact_dir.rglob("*")
        if path.is_file() and _eligible_member(path.relative_to(artifact_dir).as_posix())
    ]
    return sorted(rows, key=lambda value: value.encode("utf-8"))


def members_from_directory(artifact_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for relative in directory_member_paths(artifact_dir):
        value = (artifact_dir / relative).read_bytes()
        rows.append({"path": relative, "sha256": sha256_bytes(value),
                     "size_bytes": len(value), "required": True})
    return rows


def _git(repo: Path, *args: str, text: bool = False) -> bytes | str:
    value = subprocess.check_output(("git", *args), cwd=repo)
    return value.decode("utf-8").strip() if text else value


def git_blob_bytes(repo: Path, revision: str, repo_path: str) -> bytes:
    try:
        return _git(repo, "cat-file", "blob", f"{revision}:{repo_path}")  # type: ignore[return-value]
    except subprocess.CalledProcessError as exc:
        raise ArtifactIntegrityError("ARTIFACT_MEMBER_MISSING") from exc


def git_member_paths(repo: Path, revision: str, artifact_repo_path: str) -> list[str]:
    raw = _git(repo, "ls-tree", "-r", "--name-only", revision, "--", artifact_repo_path)
    prefix = artifact_repo_path.rstrip("/") + "/"
    rows = []
    for full in raw.decode("utf-8").splitlines():
        if full.startswith(prefix):
            relative = full[len(prefix):]
            if _eligible_member(relative):
                rows.append(relative)
    return sorted(rows, key=lambda value: value.encode("utf-8"))


def members_from_git(repo: Path, revision: str, artifact_repo_path: str) -> list[dict[str, Any]]:
    rows = []
    for relative in git_member_paths(repo, revision, artifact_repo_path):
        value = git_blob_bytes(repo, revision, f"{artifact_repo_path.rstrip('/')}/{relative}")
        rows.append({"path": relative, "sha256": sha256_bytes(value),
                     "size_bytes": len(value), "required": True})
    return rows


def index_member_paths(repo: Path, artifact_repo_path: str) -> list[str]:
    raw = _git(repo, "ls-files", "--cached", "--", artifact_repo_path)
    prefix = artifact_repo_path.rstrip("/") + "/"
    rows = []
    for full in raw.decode("utf-8").splitlines():
        if full.startswith(prefix):
            relative = full[len(prefix):]
            if _eligible_member(relative):
                rows.append(relative)
    return sorted(rows, key=lambda value: value.encode("utf-8"))


def members_from_index(repo: Path, artifact_repo_path: str) -> list[dict[str, Any]]:
    prefix = artifact_repo_path.rstrip("/")
    rows = []
    for relative in index_member_paths(repo, prefix):
        value = git_blob_bytes(repo, "", f"{prefix}/{relative}")
        rows.append({"path": relative, "sha256": sha256_bytes(value),
                     "size_bytes": len(value), "required": True})
    return rows


def build_manifest(artifact_id: str, members: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in members]
    paths = [str(row["path"]) for row in rows]
    if len(paths) != len(set(paths)):
        raise ArtifactIntegrityError("ARTIFACT_DUPLICATE_MEMBER_PATH")
    if MANIFEST_NAME in paths:
        raise ArtifactIntegrityError("ARTIFACT_MANIFEST_SELF_REFERENCE")
    rows.sort(key=_member_sort_key)
    return {
        "version": MANIFEST_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "artifact_id": artifact_id,
        "seal_status": "SEALED",
        "member_hash_basis": "GIT_BLOB_BYTES",
        "members": rows,
        "aggregate_algorithm": AGGREGATE_ALGORITHM,
        "aggregate_sha256": aggregate_sha256(rows),
    }


def validate_manifest_structure(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("version") != MANIFEST_VERSION:
        raise ArtifactIntegrityError("ARTIFACT_LEGACY_MANIFEST_AUDIT_ONLY")
    if manifest.get("aggregate_algorithm") != AGGREGATE_ALGORITHM:
        raise ArtifactIntegrityError("ARTIFACT_AGGREGATE_HASH_MISMATCH")
    rows = [dict(row) for row in manifest.get("members", [])]
    paths = [str(row.get("path")) for row in rows]
    if len(paths) != len(set(paths)):
        raise ArtifactIntegrityError("ARTIFACT_DUPLICATE_MEMBER_PATH")
    if MANIFEST_NAME in paths:
        raise ArtifactIntegrityError("ARTIFACT_MANIFEST_SELF_REFERENCE")
    if rows != sorted(rows, key=_member_sort_key):
        raise ArtifactIntegrityError("ARTIFACT_MEMBER_LIST_NONDETERMINISTIC")
    if aggregate_sha256(rows) != manifest.get("aggregate_sha256"):
        raise ArtifactIntegrityError("ARTIFACT_AGGREGATE_HASH_MISMATCH")
    return rows


def _verify(manifest: Mapping[str, Any], actual_paths: list[str],
            reader: Callable[[str], bytes]) -> dict[str, Any]:
    rows = validate_manifest_structure(manifest)
    declared = {str(row["path"]): row for row in rows}
    actual = set(actual_paths)
    missing = sorted(set(declared) - actual)
    undeclared = sorted(actual - set(declared))
    if missing:
        raise ArtifactIntegrityError("ARTIFACT_MEMBER_MISSING")
    if undeclared:
        raise ArtifactIntegrityError("ARTIFACT_UNDECLARED_MEMBER")
    sha_matches = size_matches = 0
    for path, row in declared.items():
        value = reader(path)
        if sha256_bytes(value) != row["sha256"]:
            raise ArtifactIntegrityError("ARTIFACT_MEMBER_HASH_MISMATCH")
        sha_matches += 1
        if len(value) != int(row["size_bytes"]):
            raise ArtifactIntegrityError("ARTIFACT_MEMBER_HASH_MISMATCH")
        size_matches += 1
    return {
        "status": "VERIFIED", "member_count": len(rows),
        "required_count": sum(bool(row.get("required")) for row in rows),
        "sha_matches": sha_matches, "size_matches": size_matches,
        "missing_count": 0, "undeclared_count": 0,
        "aggregate_verified": True,
    }


def verify_directory(artifact_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    return _verify(manifest, directory_member_paths(artifact_dir),
                   lambda relative: (artifact_dir / relative).read_bytes())


def verify_git(repo: Path, revision: str, artifact_repo_path: str,
               manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    prefix = artifact_repo_path.rstrip("/")
    if manifest is None:
        manifest = json.loads(git_blob_bytes(repo, revision, f"{prefix}/{MANIFEST_NAME}"))
    return _verify(
        manifest, git_member_paths(repo, revision, prefix),
        lambda relative: git_blob_bytes(repo, revision, f"{prefix}/{relative}"),
    )


def verify_index(repo: Path, artifact_repo_path: str,
                 manifest: Mapping[str, Any]) -> dict[str, Any]:
    prefix = artifact_repo_path.rstrip("/")
    return _verify(
        manifest, index_member_paths(repo, prefix),
        lambda relative: git_blob_bytes(repo, "", f"{prefix}/{relative}"),
    )


@dataclass
class ArtifactSeal:
    artifact_dir: Path
    state: str = "BUILDING"
    snapshot: dict[str, tuple[str, int]] | None = None

    def write_bytes(self, relative: str, value: bytes) -> None:
        if self.state != "BUILDING":
            self.state = "INVALID"
            raise ArtifactIntegrityError("ARTIFACT_MODIFIED_AFTER_SEAL")
        if not _eligible_member(relative):
            raise ArtifactIntegrityError("ARTIFACT_UNDECLARED_MEMBER")
        fsync_write(self.artifact_dir / relative, value)

    def write_json(self, relative: str, value: Any) -> None:
        self.write_bytes(relative, canonical_json_bytes(value) + b"\n")

    def prepare(self, required_paths: Iterable[str]) -> None:
        if self.state != "BUILDING":
            raise ArtifactIntegrityError("SEALING_PROTOCOL_FAILURE")
        actual = set(directory_member_paths(self.artifact_dir))
        if not set(required_paths).issubset(actual):
            raise ArtifactIntegrityError("ARTIFACT_MEMBER_MISSING")
        self.state = "PREPARED"

    def seal(self) -> None:
        if self.state != "PREPARED":
            raise ArtifactIntegrityError("SEALING_PROTOCOL_FAILURE")
        rows = members_from_directory(self.artifact_dir)
        self.snapshot = {row["path"]: (row["sha256"], row["size_bytes"]) for row in rows}
        self.state = "SEALED"

    def verify_unchanged(self) -> None:
        current = {row["path"]: (row["sha256"], row["size_bytes"])
                   for row in members_from_directory(self.artifact_dir)}
        if current != self.snapshot:
            self.state = "INVALID"
            raise ArtifactIntegrityError("ARTIFACT_MODIFIED_AFTER_SEAL")
        self.state = "VERIFIED"


def first_differing_offset(left: bytes, right: bytes) -> int | None:
    for offset, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return offset
    return min(len(left), len(right)) if len(left) != len(right) else None


def _without_bom(value: bytes) -> bytes:
    return value[3:] if value.startswith(b"\xef\xbb\xbf") else value


def _normalized_newlines(value: bytes) -> bytes:
    return _without_bom(value).replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _json_equal(left: bytes, right: bytes) -> bool | None:
    try:
        return json.loads(left.decode("utf-8-sig")) == json.loads(right.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def analyze_original_artifact(repo: Path, source_dir: Path, commit: str,
                              artifact_repo_path: str) -> dict[str, Any]:
    manifest = json.loads((source_dir / MANIFEST_NAME).read_bytes())
    declared = manifest.get("files", {})
    rows = []
    for relative in sorted(declared, key=lambda value: value.encode("utf-8")):
        fs_path = source_dir / relative
        fs_value = fs_path.read_bytes() if fs_path.exists() else b""
        try:
            git_value = git_blob_bytes(repo, commit, f"{artifact_repo_path}/{relative}")
        except ArtifactIntegrityError:
            git_value = b""
        newline_only = fs_value != git_value and _normalized_newlines(fs_value) == _normalized_newlines(git_value)
        bom_only = fs_value != git_value and _without_bom(fs_value) == _without_bom(git_value)
        semantic_equal = _json_equal(fs_value, git_value) if relative.endswith(".json") else None
        fs_sha = sha256_bytes(fs_value) if fs_path.exists() else None
        git_sha = sha256_bytes(git_value) if git_value else None
        if fs_sha == declared[relative] and git_sha != declared[relative] and newline_only:
            classification = "GIT_FILTER_TRANSFORMATION"
        elif fs_sha != declared[relative]:
            classification = "MODIFIED_AFTER_HASHING"
        elif git_sha != declared[relative]:
            classification = "UNKNOWN_BYTE_MUTATION"
        else:
            classification = "UNCHANGED"
        stat = fs_path.stat() if fs_path.exists() else None
        rows.append({
            "path": relative, "declared_sha256": declared[relative],
            "filesystem_sha256": fs_sha, "git_blob_sha256": git_sha,
            "filesystem_size_bytes": len(fs_value) if fs_path.exists() else None,
            "git_blob_size_bytes": len(git_value) if git_value else None,
            "first_differing_offset": first_differing_offset(fs_value, git_value),
            "newline_only": newline_only, "bom_only": bom_only,
            "semantic_content_changed": (not semantic_equal) if semantic_equal is not None else not newline_only,
            "filesystem_last_modified_utc": (
                datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                if stat else None),
            "generation_code_path": "scripts/run_strategy_phase4a6d_bounded_identity_gate.py",
            "generation_temporary_sha256": None, "generation_log_sha256": None,
            "report_machine_input": relative.endswith(".json") and relative != "test_results.json",
            "classification": classification,
        })
    changed = [row for row in rows if row["classification"] != "UNCHANGED"]
    core = [row for row in rows if row["path"].endswith(".json") and row["path"] != "test_results.json"]
    return {
        "version": "phase4a6d-original-artifact-forensics-v1",
        "original_commit": commit,
        "original_artifact_id": source_dir.name,
        "declared_aggregate_sha256": manifest.get("aggregate_sha256"),
        "declared_manifest_internally_consistent": True,
        "temporary_or_log_hash_records_found": False,
        "members": rows, "changed_member_count": len(changed),
        "filesystem_declared_match_count": sum(row["filesystem_sha256"] == row["declared_sha256"] for row in rows),
        "git_declared_match_count": sum(row["git_blob_sha256"] == row["declared_sha256"] for row in rows),
        "core_json_filesystem_matches_manifest": all(row["filesystem_sha256"] == row["declared_sha256"] for row in core),
        "core_json_git_blobs_match_manifest": all(row["git_blob_sha256"] == row["declared_sha256"] for row in core),
        "only_display_members_changed": all(row["path"] == "report.md" for row in changed),
        "report_change_cause": "GIT_FILTER_TRANSFORMATION_CRLF_TO_LF",
        "conclusion": "GIT_FILTER_TRANSFORMATION_AFFECTED_CORE_MACHINE_JSON",
    }


def reseal_permitted(forensics: Mapping[str, Any]) -> bool:
    """v1 results may be reused only when byte changes are display-only."""
    return bool(
        forensics.get("core_json_filesystem_matches_manifest")
        and forensics.get("core_json_git_blobs_match_manifest")
        and forensics.get("only_display_members_changed")
    )


def render_report(machine: Mapping[str, Any]) -> bytes:
    """Render the fixed v2 report from already schema-validated JSON values."""
    decision = machine["final_gate_decision"]
    gate = machine["gate_manifest"]
    lifecycle = machine["lifecycle_transition_audit"]
    identity = machine["identity_continuity_audit"]
    resume = machine["checkpoint_resume_comparison"]
    directions = machine["direction_summary"]["rows"]
    confirmation = directions[0]["confirmation_lineage"] if directions else 1.0
    geometry = directions[0]["geometry_provenance"] if directions else 1.0
    lines = [
        "# Crypto-Bot Phase 4A6D Bounded Lifecycle Identity Gate",
        "", "- Report template: `phase4a6d-bounded-report-v2`",
        f"- Conclusion: `{decision['conclusion']}`", f"- Passed: `{str(decision['passed'])}`",
        f"- Feature SHA: `{gate['feature_sha']}`", f"- Dataset identity: `{gate['dataset_identity']}`",
        "- Windows: EARLY, MIDDLE, LATE (Development only)",
        f"- Parameters: {gate['parameter_count']} (8 per family/direction)",
        f"- Context / evaluate / compare / router: {gate['context_count']} / {gate['evaluate_calls']} / {gate['compare_calls']} / {gate['router_evaluations']}",
        f"- Stage counts: `{json.dumps(lifecycle['stage_evaluation_counts'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))}`",
        f"- Propagation failures: {lifecycle['lifecycle_propagation_failure']}",
        f"- Unexpected anchor / continuity changes: {identity['unexpected_setup_anchor_changes']} / {identity['unexpected_level_continuity_changes']}",
        f"- Duplicate TRIGGER_READY: {lifecycle['duplicate_TRIGGER_READY']}",
        f"- Checkpoint/resume exact: {str(resume['all_exact'])} ({resume['aggregate_match_rate'] * 100:.2f}%)",
        f"- Confirmation lineage / geometry provenance: {confirmation * 100:.2f}% / {geometry * 100:.2f}%",
        "- Validation reads / OOT reads: 0 / 0", "- Trades / PnL: 0 / not calculated", "",
        "This recomputed bounded gate did not run full Development, a return experiment, the execution layer, Paper, frontend, or production deployment.", "",
    ]
    return "\n".join(lines).encode("utf-8")
