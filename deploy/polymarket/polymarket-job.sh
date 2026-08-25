#!/usr/bin/env bash
set -euo pipefail

job="${1:-}"
case "$job" in
  collect) service=polymarket-collector ;;
  backup) service=polymarket-backup ;;
  *) echo "usage: $0 collect|backup" >&2; exit 2 ;;
esac

: "${POLYMARKET_RELEASE_DIR:?POLYMARKET_RELEASE_DIR is required}"
: "${POLYMARKET_RUNTIME_ENV:?POLYMARKET_RUNTIME_ENV is required}"
base="$POLYMARKET_RELEASE_DIR/source/deploy/compose/ai6b-production-candidate.yml"
runtime="$POLYMARKET_RELEASE_DIR/deployment/runtime.override.yml"
edge="$POLYMARKET_RELEASE_DIR/deployment/public-edge.override.yml"
feature="$POLYMARKET_RELEASE_DIR/source/deploy/compose/polymarket-production.override.yml"
for path in "$POLYMARKET_RUNTIME_ENV" "$base" "$runtime" "$edge" "$feature"; do
  test -r "$path" || { echo "required deployment file missing: $path" >&2; exit 2; }
done

exec /usr/bin/docker compose --project-name crypto-bot \
  --env-file "$POLYMARKET_RUNTIME_ENV" \
  -f "$base" -f "$runtime" -f "$edge" -f "$feature" \
  --profile polymarket-ops run --rm --no-deps "$service"
