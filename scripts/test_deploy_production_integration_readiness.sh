#!/usr/bin/env bash
set -Eeuo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
count_file="$tmp/curl-count"

cat > "$tmp/docker" <<'EOF'
#!/usr/bin/env bash
if [[ $1 == inspect ]]; then
  if [[ $3 == '{{.State.Running}}' ]]; then echo true; exit 0; fi
  echo healthy
  exit 0
fi
if [[ $1 == exec ]]; then exit 0; fi
exit 1
EOF
cat > "$tmp/curl" <<EOF
#!/usr/bin/env bash
count=0
[[ -f '$count_file' ]] && count=\$(cat '$count_file')
count=\$((count + 1))
printf '%s' "\$count" > '$count_file'
if (( count == 1 )); then exit 35; fi
printf '200'
EOF
cat > "$tmp/sleep" <<'EOF'
#!/usr/bin/env bash
:
EOF
chmod +x "$tmp/docker" "$tmp/curl" "$tmp/sleep"

PATH="$tmp:$PATH" PROMOTION_READINESS_GRACE_SECONDS=0 PROMOTION_READINESS_RETRY_SECONDS=0 PROMOTION_READINESS_TIMEOUT_SECONDS=5 \
  bash -c 'source "$1"; wait_for_promotion_readiness' _ "$root/scripts/deploy_production_integration.sh"
[[ $(cat "$count_file") == 2 ]]
printf 'readiness retry test: PASS\n'
