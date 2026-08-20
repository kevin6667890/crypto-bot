#!/usr/bin/env sh
set -eu
cd /opt/crypto-bot
set -a
. /opt/crypto-bot-ai6b-staging/29cfedd-exact/deployment/29cfedd-enabled.env
set +a
test -f "$TLS_CERTIFICATE_FILE"
test -f "$TLS_PRIVATE_KEY_FILE"
docker compose -f docker-compose.yml -f deploy/compose/production-https.yml up -d --no-deps --force-recreate frontend
