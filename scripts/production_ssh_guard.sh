#!/usr/bin/env bash
# Run this before any production SSH/deploy/rollback/smoke command.
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
TARGET="${1:-}"
EXPECTED_REVISION="${2:-}"
python3 "$ROOT/scripts/production_origin_guard.py" --target "$TARGET"

if [[ -n "$EXPECTED_REVISION" && ! "$EXPECTED_REVISION" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "PRODUCTION_REVISION_INVALID" >&2
  exit 2
fi

SSH_ARGS=(-o BatchMode=yes -o StrictHostKeyChecking=yes)
if [[ -n "${PRODUCTION_SSH_IDENTITY_FILE:-}" ]]; then
  SSH_ARGS+=(-i "$PRODUCTION_SSH_IDENTITY_FILE")
fi

REMOTE_CHECK='set -eu; test -d /opt/crypto-bot; test -d /opt/crypto-bot-ai6b-staging; git -C /opt/crypto-bot rev-parse --is-inside-work-tree >/dev/null; hostname'
if [[ -n "$EXPECTED_REVISION" ]]; then
  REMOTE_CHECK+="; git -C /opt/crypto-bot cat-file -e ${EXPECTED_REVISION}^{commit}"
fi

ssh "${SSH_ARGS[@]}" "root@$TARGET" "$REMOTE_CHECK"
