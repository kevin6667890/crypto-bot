#!/bin/sh
set -eu

stage=/opt/crypto-bot-ai6b-staging/af7e57b
evidence="$stage/b4/evidence"
canonical=/opt/crypto-bot-canonical-staging/35a5b29/deploy-compose-35a5b29.yml
candidate="$stage/source/deploy/compose/ai6b-production-candidate.yml"
exact="$stage/deployment/exact-candidate.override.yml"
public=/opt/crypto-bot-ai6b-staging/public-edge/deploy/compose/public-frontend-edge.override.yml
active="$stage/b4/b4-active.override.yml"
env_file="$stage/deployment/b3-disabled.env"
export PUBLIC_EDGE_NGINX_CONFIG=/opt/crypto-bot-ai6b-staging/public-edge/runtime/nginx.conf
export COMPOSE_PROFILES=ai6b-candidate

dc() {
  docker compose --env-file "$env_file" -p crypto-bot \
    -f "$canonical" -f "$candidate" -f "$exact" -f "$public" "$@"
}

dc_active() {
  docker compose --env-file "$env_file" -p crypto-bot \
    -f "$canonical" -f "$candidate" -f "$exact" -f "$public" -f "$active" "$@"
}

install -d -o 10001 -g 10001 -m 0750 "$evidence"
dc_active config > "$evidence/rendered-config.sanitized.yml"
dc_active up -d --no-deps --force-recreate paper-api report-worker audit-worker
for service in paper-api report-worker audit-worker; do
  container=$(dc_active ps -q "$service")
  for attempt in $(seq 1 30); do
    [ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" = healthy ] && break
    sleep 2
  done
  [ "$(docker inspect "$container" --format '{{.State.Health.Status}}')" = healthy ]
done
dc_active up -d --no-deps --force-recreate b4-supervisor

container=ai6b-b4-supervisor-af7e57b
while [ "$(docker inspect "$container" --format '{{.State.Running}}')" = true ]; do
  python3 "$stage/b4/host_sample.py" >> "$evidence/runtime-samples.jsonl"
  sleep 60
done

exit_code=$(docker inspect "$container" --format '{{.State.ExitCode}}')
python3 "$stage/b4/host_sample.py" >> "$evidence/runtime-samples.jsonl"
printf '%s\n' "$exit_code" > "$evidence/supervisor-exit-code.txt"

dc up -d --no-deps --force-recreate paper-api
dc up --no-start --no-deps --force-recreate report-worker audit-worker
exit "$exit_code"
