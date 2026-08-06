# Artifact integrity manifest v2

`artifact-integrity-manifest-v2` makes the bytes stored in Git blobs the
authoritative evidence (`member_hash_basis = GIT_BLOB_BYTES`). The manifest is
written only after the ordinary members have been committed.

Members are relative POSIX paths, unique, and sorted by their UTF-8 path bytes.
The list excludes `sha256_manifest.json`, temporary files, checkpoint locks,
and hidden system files. Every required member must exist and no undeclared
member may exist.

The `sha256-canonical-member-list-v1` aggregate input is an array containing
only `path`, `sha256`, and `size_bytes` for every member. The array is path
sorted, serialized as UTF-8 JSON with keys sorted, compact separators, LF
semantics, and no trailing byte. The aggregate is the lowercase SHA-256 hex
digest of those exact bytes. It does not recursively cover the manifest.

The sealing lifecycle is `BUILDING -> PREPARED -> SEALED -> VERIFIED`.
Member writes are allowed only in `BUILDING`; post-seal writes or detected
changes produce `ARTIFACT_MODIFIED_AFTER_SEAL` and transition to `INVALID`.
Verification rejects missing, undeclared, hash-mismatched, size-mismatched, or
aggregate-mismatched evidence rather than refreshing hashes.

Final verification reads each member with `git cat-file blob` at the final
evidence commit. The index, the first members commit, the final manifest
commit, a clean worktree, and a directory materialized directly from Git blobs
are checked independently. The scoped `.gitattributes` rule disables text
conversion only below the Phase 4A6D bounded artifact directory.
