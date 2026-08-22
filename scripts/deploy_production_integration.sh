#!/usr/bin/env bash
# Build and promote an immutable integration artifact without mutating the active one.
set -euo pipefail

host=${PRODUCTION_ORIGIN_HOST:-8.217.62.226}
user=${PRODUCTION_ORIGIN_USER:-root}
revision=${1:?usage: deploy_production_integration.sh <git-revision>}
root=/opt/crypto-bot-staging
artifact="$root/$revision"
key=${DEPLOY_SSH_KEY:-$HOME/.ssh/crypto_bot_codex_deploy}
ssh_base=(ssh -i "$key" -o BatchMode=yes -o StrictHostKeyChecking=yes "$user@$host")

[[ $host == 8.217.62.226 ]] || { echo "refusing non-canonical production target" >&2; exit 2; }
git cat-file -e "$revision^{commit}"
git diff --check "$revision^" "$revision"

# The active staging artifact is never changed.  Its labels are the source of truth
# for the complete override order, including AI6B and public-edge overlays.
active=$(${ssh_base[@]} "docker inspect crypto-bot-paper-api-1 -f '{{ index .Config.Labels \"com.docker.compose.project.config_files\" }}'")
[[ -n $active ]] || { echo "active compose topology unavailable" >&2; exit 3; }
${ssh_base[@]} "test ! -e '$artifact'; mkdir -p '$artifact/source' '$artifact/deployment'; printf '%s' '$active' > '$artifact/deployment/active-compose-files'"

# Source is materialized from the requested immutable Git commit, never copied over
# the active source. Deployment metadata is copied from the active artifact only.
git archive --format=tar "$revision" | "${ssh_base[@]}" "tar -x -C '$artifact/source'"
${ssh_base[@]} "base=\$(printf '%s' '$active' | cut -d, -f1 | sed 's#/source/docker-compose.yml##'); cp -a \"\$base/deployment/.\" '$artifact/deployment/' 2>/dev/null || true"

${ssh_base[@]} "cd '$artifact/source'; docker build --pull -t crypto-bot-integration-app:'$revision' .; docker build --pull -t crypto-bot-integration-frontend:'$revision' -f frontend/Dockerfile ."

# Rebuild the exact active compose list, substituting only the old source path with
# this new immutable source. The research worker is deliberately excluded from up.
${ssh_base[@]} "set -eu; old=\$(printf '%s' '$active' | cut -d, -f1 | sed 's#/source/docker-compose.yml##'); files=\$(printf '%s' '$active' | sed \"s#\$old/source#$artifact/source#g\"); env='$artifact/deployment/integration.env'; cp '$artifact/deployment/'*.env \"\$env\" 2>/dev/null || :; printf '\nAI6B_APP_IMAGE=crypto-bot-integration-app:$revision\nAI6B_FRONTEND_IMAGE=crypto-bot-integration-frontend:$revision\nFACTOR_PROGRAM_GIT_COMMIT=$revision\nLIVE_TRADING_ENABLED=false\n' >> \"\$env\"; args=(); IFS=, read -ra paths <<< \"\$files\"; for f in \"\${paths[@]}\"; do args+=(-f \"\$f\"); done; docker compose --env-file \"\$env\" \"\${args[@]}\" config -q; docker compose --env-file \"\$env\" \"\${args[@]}\" up -d --no-deps paper-api frontend report-worker audit-worker; docker compose --env-file \"\$env\" \"\${args[@]}\" ps"

${ssh_base[@]} "curl -fsSk https://127.0.0.1/ >/dev/null; docker inspect -f '{{.State.Health.Status}}' crypto-bot-frontend-1 | grep healthy; docker inspect -f '{{.State.Health.Status}}' crypto-bot-paper-api-1 | grep healthy; docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' crypto-bot-research-worker-1 | grep '^LIVE_TRADING_ENABLED=false\|^AUTO_RESEARCH_ENABLED=false' >/dev/null || true"
echo "DEPLOYED_ARTIFACT=$artifact"
