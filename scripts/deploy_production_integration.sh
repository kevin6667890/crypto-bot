#!/usr/bin/env bash
# Must run on the production host from a disposable Git checkout.
set -Eeuo pipefail
trap 'rc=$?; printf "ERROR line=%s exit=%s command=%q\n" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR
stage(){ printf 'STAGE %s\n' "$1"; }
wait_for_promotion_readiness() {
  local grace_seconds=${PROMOTION_READINESS_GRACE_SECONDS:-6}
  local retry_seconds=${PROMOTION_READINESS_RETRY_SECONDS:-2}
  local timeout_seconds=${PROMOTION_READINESS_TIMEOUT_SECONDS:-60}
  local deadline frontend_health paper_health frontend_running paper_running status

  printf 'READINESS grace=%ss retry=%ss timeout=%ss\n' "$grace_seconds" "$retry_seconds" "$timeout_seconds"
  sleep "$grace_seconds"
  deadline=$((SECONDS + timeout_seconds))
  while (( SECONDS < deadline )); do
    frontend_running=$(docker inspect -f '{{.State.Running}}' crypto-bot-frontend-1)
    paper_running=$(docker inspect -f '{{.State.Running}}' crypto-bot-paper-api-1)
    frontend_health=$(docker inspect -f '{{.State.Health.Status}}' crypto-bot-frontend-1)
    paper_health=$(docker inspect -f '{{.State.Health.Status}}' crypto-bot-paper-api-1)
    printf 'READINESS frontend=%s/%s paper-api=%s/%s\n' "$frontend_running" "$frontend_health" "$paper_running" "$paper_health"
    if [[ $frontend_health == unhealthy || $paper_health == unhealthy ]]; then
      printf 'READINESS unhealthy container\n' >&2
      return 1
    fi
    if [[ $frontend_running == true && $paper_running == true && $frontend_health == healthy && $paper_health == healthy ]]; then
      if docker exec crypto-bot-frontend-1 wget --no-check-certificate -q -O /dev/null https://127.0.0.1:8443/; then
        if status=$(curl -sk --connect-timeout 3 --max-time 8 -o /dev/null -w '%{http_code}' https://127.0.0.1/); then
          if [[ $status == 200 ]]; then
            printf 'READINESS https=200\n'
            return 0
          fi
          printf 'READINESS unexpected HTTPS status=%s\n' "$status" >&2
          return 1
        fi
        printf 'READINESS transient TLS failure\n'
      else
        printf 'READINESS frontend internal probe pending\n'
      fi
    fi
    sleep "$retry_seconds"
  done
  printf 'READINESS timeout after %ss\n' "$timeout_seconds" >&2
  return 1
}

main() {
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
revision=${1:?usage: $0 <revision> [--validate-only|--promote]}
mode=${2:---validate-only}
[[ $mode == --validate-only || $mode == --promote ]] || exit 2
[[ ${PRODUCTION_ORIGIN_HOST:-8.217.62.226} == 8.217.62.226 ]] || exit 2
root=/opt/crypto-bot-staging; artifact="$root/$revision"
evidence="/opt/crypto-bot-deploy-runner/deployment-validation/$revision"
mkdir -p "$evidence"
[[ $PWD != /opt/crypto-bot-ai6b-staging/c110869-exact/source* ]] || { echo "refusing active artifact" >&2; exit 3; }
stage lineage; git cat-file -e "$revision^{commit}"; git diff --check "$revision^" "$revision"

stage active-topology
active=$(docker inspect crypto-bot-paper-api-1 -f '{{ index .Config.Labels "com.docker.compose.project.config_files" }}')
env_file=$(docker inspect crypto-bot-paper-api-1 -f '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}')
project=$(docker inspect crypto-bot-paper-api-1 -f '{{ index .Config.Labels "com.docker.compose.project" }}')
[[ -n $active && -n $env_file && -n $project ]] || { echo "active topology unavailable" >&2; exit 4; }
old=$(printf '%s' "$active" | cut -d, -f1 | sed 's#/source/docker-compose.yml##')
IFS=, read -ra active_paths <<< "$active"
base_source=""
for active_path in "${active_paths[@]}"; do
  if [[ $active_path == */deploy/compose/ai6b-production-candidate.yml ]]; then
    base_source=${active_path%/deploy/compose/ai6b-production-candidate.yml}
    break
  fi
done
[[ -n $base_source && -f $base_source/dashboard/paper_api.py ]] || { echo "active AI6B source unavailable" >&2; exit 4; }
if [[ ! -e $artifact ]]; then
  mkdir -p "$artifact/source" "$artifact/deployment"
  cp -a "$base_source/." "$artifact/source/"
  git diff --binary c110869 "$revision" -- dashboard/paper_api.py dashboard/research_repository.py frontend/src/DiscoveryLab.tsx | git -C "$artifact/source" apply --check
  git diff --binary c110869 "$revision" -- dashboard/paper_api.py dashboard/research_repository.py frontend/src/DiscoveryLab.tsx | git -C "$artifact/source" apply
  cp -a "$old/deployment/." "$artifact/deployment/"
  cp "$env_file" "$artifact/deployment/integration.env"
  printf '\nAI6B_APP_IMAGE=crypto-bot-integration-app:%s\nAI6B_FRONTEND_IMAGE=crypto-bot-integration-frontend:%s\nFACTOR_PROGRAM_GIT_COMMIT=%s\nLIVE_TRADING_ENABLED=false\n' "$revision" "$revision" "$revision" >> "$artifact/deployment/integration.env"
  cp "$artifact/deployment/integration.env" "$artifact/source/.env"
else
  test -f "$artifact/source/dashboard/paper_api.py" && test -f "$artifact/deployment/integration.env" || { echo "existing artifact is incomplete" >&2; exit 4; }
  cp "$artifact/deployment/integration.env" "$artifact/source/.env"
fi

stage frontend-build
docker run --rm -v "$artifact/source:/workspace" -w /workspace/frontend node:20-alpine sh -ec 'npm ci --ignore-scripts; npm run build'
stage immutable-images; cd "$artifact/source"
docker build --pull -t "crypto-bot-integration-app:$revision" .
docker build --pull -t "crypto-bot-integration-frontend:$revision" -f frontend/Dockerfile .
stage compose-reconstruction
files=${active//"$old/source"/"$artifact/source"}; args=(--project-name "$project" --profile ai6b-candidate --env-file "$artifact/deployment/integration.env")
IFS=, read -ra paths <<< "$files"; : > "$evidence/compose-files.txt"; for file in "${paths[@]}"; do test -f "$file"; printf '%s\n' "$file" >> "$evidence/compose-files.txt"; args+=(-f "$file"); done
cat "$evidence/compose-files.txt"
stage compose-config
docker compose "${args[@]}" config -q
docker compose "${args[@]}" config > "$evidence/compose-config.yml"
stage topology-validation
grep -Eq 'published: ?"?443"?' "$evidence/compose-config.yml"
grep -Eq 'target: ?8443' "$evidence/compose-config.yml"
grep -q 'paper-api:' "$evidence/compose-config.yml"
grep -q '8765' "$evidence/compose-config.yml"
grep -q 'audit-worker:' "$evidence/compose-config.yml"
grep -q 'report-worker:' "$evidence/compose-config.yml"
grep -q 'research-worker:' "$evidence/compose-config.yml"
grep -q '/var/lib/paper' "$evidence/compose-config.yml"
grep -q '^volumes:' "$evidence/compose-config.yml"
grep -q '^secrets:' "$evidence/compose-config.yml"
grep -q 'tls_certificate' "$evidence/compose-config.yml"
! grep -Eq 'published: ?"?8501"?' "$evidence/compose-config.yml"
grep -q 'LIVE_TRADING_ENABLED=false' "$artifact/deployment/integration.env"
printf 'revision=%s\nmode=%s\ncompose_project=%s\nconfig=PASS\nfrontend_port=443->8443\npaper_api=internal_8765\nai6b_workers=audit-worker,report-worker\nresearch_worker=preserved\nvolumes=preserved\nsecrets=preserved\npublic_8501=absent\nlive_trading=false\n' "$revision" "$mode" "$project" > "$evidence/validation-summary.txt"
printf '%s\n' "$active" > "$artifact/deployment/previous-compose-files"
docker image inspect "crypto-bot-integration-app:$revision" --format '{{index .Id}}' > "$evidence/image-digests.txt"
docker image inspect "crypto-bot-integration-frontend:$revision" --format '{{index .Id}}' >> "$evidence/image-digests.txt"
[[ $mode == --validate-only ]] && { echo "VALIDATED_ARTIFACT=$artifact EVIDENCE=$evidence"; exit 0; }

# The durable research worker remains untouched; it is the only research queue owner.
docker compose "${args[@]}" up -d --no-deps paper-api frontend
stage promotion-readiness
wait_for_promotion_readiness | tee "$evidence/promotion-readiness.txt"
docker inspect crypto-bot-research-worker-1 -f '{{.State.Running}}' | grep true
docker exec crypto-bot-paper-api-1 python -c "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8765/api/automatic-research', timeout=10).status == 200"
echo "DEPLOYED_ARTIFACT=$artifact"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
