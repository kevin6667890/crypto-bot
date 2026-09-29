#!/usr/bin/env bash
# Refresh only confirmed, closed OKX candles used by the causal research cache.
# This is intentionally serialized: the live Paper API shares the SQLite WAL.
set -euo pipefail

container_name="${CANONICAL_OHLCV_CONTAINER:-crypto-bot-paper-api-1}"
database_path="${CANONICAL_OHLCV_DATABASE:-/var/lib/paper/paper_trades.db}"
lock_file="${CANONICAL_OHLCV_LOCK:-/var/lock/crypto-bot-canonical-ohlcv.lock}"

exec 9>"$lock_file"
flock -n 9 || { echo "canonical OHLCV refresh already running"; exit 0; }

docker inspect --format '{{.State.Running}}' "$container_name" | grep -qx true

latest_candle() {
  docker exec "$container_name" python -c '
import sqlite3, sys
connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
row = connection.execute(
    "SELECT COALESCE(MAX(ts), 0) FROM historical_candles "
    "WHERE instrument=? AND timeframe=? AND confirmed=1",
    (sys.argv[2], sys.argv[3]),
).fetchone()
print(row[0])
' "$database_path" "$1" "$2"
}

refresh_partition() {
  local instrument="$1" timeframe="$2" step="$3"
  local latest start end
  latest="$(latest_candle "$instrument" "$timeframe")"
  start=$((latest + step))
  end=$(( $(date -u +%s) / step * step ))
  if (( start >= end )); then
    printf 'CURRENT instrument=%s timeframe=%s latest=%s\n' "$instrument" "$timeframe" "$latest"
    return
  fi
  docker exec "$container_name" python scripts/materialize_canonical_ohlcv.py \
    --database "$database_path" --instrument "$instrument" --timeframe "$timeframe" \
    --start "$start" --end "$end"
}

for instrument in BTC-USDT ETH-USDT SOL-USDT; do
  refresh_partition "$instrument" 15m 900
  refresh_partition "$instrument" 1H 3600
  refresh_partition "$instrument" 4H 14400
done
