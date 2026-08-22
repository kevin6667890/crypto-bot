#!/usr/bin/env bash
# Must run on the production host from a disposable Git checkout.
set -euo pipefail
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
revision=${1:?usage: $0 <revision> [--validate-only|--promote]}
mode=${2:---validate-only}
[[ $mode == --validate-only || $mode == --promote ]] || exit 2
[[ ${PRODUCTION_ORIGIN_HOST:-8.217.62.226} == 8.217.62.226 ]] || exit 2
root=/opt/crypto-bot-staging; artifact="$root/$revision"
[[ $PWD != /opt/crypto-bot-ai6b-staging/c110869-exact/source* ]] || { echo "refusing active artifact" >&2; exit 3; }
git cat-file -e "$revision^{commit}"; git diff --check "$revision^" "$revision"

active=$(docker inspect crypto-bot-paper-api-1 -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}')
env_file=$(docker inspect crypto-bot-paper-api-1 -f '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}')
[[ -n $active && -n $env_file ]] || { echo "active topology unavailable" >&2; exit 4; }
old=$(printf '%s' "$active" | cut -d, -f1 | sed 's#/source/docker-compose.yml##')
if [[ ! -e $artifact ]]; then
  mkdir -p "$artifact/source" "$artifact/deployment"
  git archive --format=tar "$revision" | tar -x -C "$artifact/source"
  cp -a "$old/deployment/." "$artifact/deployment/"
  cp "$env_file" "$artifact/deployment/integration.env"
  printf '\nAI6B_APP_IMAGE=crypto-bot-integration-app:%s\nAI6B_FRONTEND_IMAGE=crypto-bot-integration-frontend:%s\nFACTOR_PROGRAM_GIT_COMMIT=%s\nLIVE_TRADING_ENABLED=false\n' "$revision" "$revision" "$revision" >> "$artifact/deployment/integration.env"
else
  test -f "$artifact/source/dashboard/paper_api.py" && test -f "$artifact/deployment/integration.env" || { echo "existing artifact is incomplete" >&2; exit 4; }
fi

cd "$artifact/source"
docker build --pull -t "crypto-bot-integration-app:$revision" .
docker build --pull -t "crypto-bot-integration-frontend:$revision" -f frontend/Dockerfile .
files=${active//"$old\/source"/"$artifact\/source"}; args=(--profile ai6b-candidate --env-file "$artifact/deployment/integration.env")
IFS=, read -ra paths <<< "$files"; for file in "${paths[@]}"; do args+=(-f "$file"); done
docker compose "${args[@]}" config -q
config=$(docker compose "${args[@]}" config)
grep -q '443:8443' <<< "$config"; grep -q '/var/lib/paper' <<< "$config"; grep -q 'research-worker:' <<< "$config"; grep -q 'LIVE_TRADING_ENABLED: "false"' <<< "$config" || true
printf '%s\n' "$active" > "$artifact/deployment/previous-compose-files"
docker image inspect "crypto-bot-ai6b-app:74c6c8e" --format '{{index .RepoDigests 0}}' > "$artifact/deployment/previous-paper-api-image" 2>/dev/null || true
docker image inspect "crypto-bot-ai6b-frontend:c110869" --format '{{index .RepoDigests 0}}' > "$artifact/deployment/previous-frontend-image" 2>/dev/null || true
[[ $mode == --validate-only ]] && { echo "VALIDATED_ARTIFACT=$artifact"; exit 0; }

# The durable research worker remains untouched; it is the only research queue owner.
docker compose "${args[@]}" up -d --no-deps paper-api frontend report-worker audit-worker
curl -fsSk https://127.0.0.1/ >/dev/null
[[ $(docker inspect -f '{{.State.Health.Status}}' crypto-bot-frontend-1) == healthy ]]
[[ $(docker inspect -f '{{.State.Health.Status}}' crypto-bot-paper-api-1) == healthy ]]
docker inspect crypto-bot-research-worker-1 -f '{{.State.Running}}' | grep true
curl -fsS http://127.0.0.1:8765/api/automatic-research >/dev/null
echo "DEPLOYED_ARTIFACT=$artifact"
