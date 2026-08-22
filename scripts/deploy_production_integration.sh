#!/usr/bin/env bash
# Must run on the production host from a disposable Git checkout.
set -Eeuo pipefail
trap 'rc=$?; printf "ERROR line=%s exit=%s command=%q\n" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR
stage(){ printf 'STAGE %s\n' "$1"; }
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
[[ -n $active && -n $env_file ]] || { echo "active topology unavailable" >&2; exit 4; }
old=$(printf '%s' "$active" | cut -d, -f1 | sed 's#/source/docker-compose.yml##')
if [[ ! -e $artifact ]]; then
  mkdir -p "$artifact/source" "$artifact/deployment"
  git archive --format=tar "$revision" | tar -x -C "$artifact/source"
  cp -a "$old/deployment/." "$artifact/deployment/"
  cp "$env_file" "$artifact/deployment/integration.env"
  printf '\nAI6B_APP_IMAGE=crypto-bot-integration-app:%s\nAI6B_FRONTEND_IMAGE=crypto-bot-integration-frontend:%s\nFACTOR_PROGRAM_GIT_COMMIT=%s\nLIVE_TRADING_ENABLED=false\n' "$revision" "$revision" "$revision" >> "$artifact/deployment/integration.env"
  cp "$artifact/deployment/integration.env" "$artifact/source/.env"
else
  test -f "$artifact/source/dashboard/paper_api.py" && test -f "$artifact/deployment/integration.env" || { echo "existing artifact is incomplete" >&2; exit 4; }
  cp "$artifact/deployment/integration.env" "$artifact/source/.env"
fi

stage immutable-images; cd "$artifact/source"
docker build --pull -t "crypto-bot-integration-app:$revision" .
docker build --pull -t "crypto-bot-integration-frontend:$revision" -f frontend/Dockerfile .
stage compose-reconstruction
files=${active//"$old/source"/"$artifact/source"}; args=(--profile ai6b-candidate --env-file "$artifact/deployment/integration.env")
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
printf 'revision=%s\nmode=%s\nconfig=PASS\nfrontend_port=443->8443\npaper_api=internal_8765\nai6b_workers=audit-worker,report-worker\nresearch_worker=preserved\nvolumes=preserved\nsecrets=preserved\npublic_8501=absent\nlive_trading=false\n' "$revision" "$mode" > "$evidence/validation-summary.txt"
printf '%s\n' "$active" > "$artifact/deployment/previous-compose-files"
docker image inspect "crypto-bot-integration-app:$revision" --format '{{index .Id}}' > "$evidence/image-digests.txt"
docker image inspect "crypto-bot-integration-frontend:$revision" --format '{{index .Id}}' >> "$evidence/image-digests.txt"
[[ $mode == --validate-only ]] && { echo "VALIDATED_ARTIFACT=$artifact EVIDENCE=$evidence"; exit 0; }

# The durable research worker remains untouched; it is the only research queue owner.
docker compose "${args[@]}" up -d --no-deps paper-api frontend report-worker audit-worker
curl -fsSk https://127.0.0.1/ >/dev/null
[[ $(docker inspect -f '{{.State.Health.Status}}' crypto-bot-frontend-1) == healthy ]]
[[ $(docker inspect -f '{{.State.Health.Status}}' crypto-bot-paper-api-1) == healthy ]]
docker inspect crypto-bot-research-worker-1 -f '{{.State.Running}}' | grep true
curl -fsS http://127.0.0.1:8765/api/automatic-research >/dev/null
echo "DEPLOYED_ARTIFACT=$artifact"
