"""Bounded, resumable OKX derivative backfill and immutable snapshot builder.

No request is made unless the operator supplies an explicit UTC [start, end]
range. Raw response fingerprints and request cursors are retained for audit.
The script never fabricates CVD and never fills missing derivative observations.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


VERSION = "thesis-derivatives-backfill-v1"
SOURCE_VERSION = "okx-public-api-v5"
BASE_URL = "https://www.okx.com"
INSTRUMENTS = {"BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"}
OI_PERIODS = {"5m": 300_000, "1H": 3_600_000, "1D": 86_400_000}
MAX_RANGE_DAYS = 1_500
ENDPOINTS = {
    "oi": "/api/v5/rubik/stat/contracts/open-interest-history",
    "funding": "/api/v5/public/funding-rate-history",
    "mark": "/api/v5/market/history-mark-price-candles",
    "index": "/api/v5/market/history-index-candles",
}


def utc_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include UTC offset/Z")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=FULL;
    CREATE TABLE IF NOT EXISTS derivative_observations(
      data_type TEXT NOT NULL,instrument TEXT NOT NULL,source_ts_ms INTEGER NOT NULL,
      value REAL NOT NULL,unit TEXT NOT NULL,aux_json TEXT NOT NULL DEFAULT '{}',
      source TEXT NOT NULL,source_version TEXT NOT NULL,ingested_at_ms INTEGER NOT NULL,
      response_sha256 TEXT NOT NULL,PRIMARY KEY(data_type,instrument,source_ts_ms));
    CREATE TABLE IF NOT EXISTS source_price_observations(
      price_type TEXT NOT NULL,instrument TEXT NOT NULL,source_ts_ms INTEGER NOT NULL,
      close REAL NOT NULL,confirmed INTEGER NOT NULL,source TEXT NOT NULL,
      source_version TEXT NOT NULL,ingested_at_ms INTEGER NOT NULL,response_sha256 TEXT NOT NULL,
      PRIMARY KEY(price_type,instrument,source_ts_ms));
    CREATE TABLE IF NOT EXISTS backfill_checkpoints(
      lane TEXT NOT NULL,instrument TEXT NOT NULL,period TEXT NOT NULL,start_ms INTEGER NOT NULL,
      end_ms INTEGER NOT NULL,next_cursor_ms INTEGER,status TEXT NOT NULL,pages INTEGER NOT NULL DEFAULT 0,
      retries INTEGER NOT NULL DEFAULT 0,last_response_sha256 TEXT,updated_at_ms INTEGER NOT NULL,
      PRIMARY KEY(lane,instrument,period,start_ms,end_ms));
    CREATE TABLE IF NOT EXISTS source_responses(
      response_sha256 TEXT PRIMARY KEY,lane TEXT NOT NULL,instrument TEXT NOT NULL,
      request_url TEXT NOT NULL,row_count INTEGER NOT NULL,min_source_ts_ms INTEGER,
      max_source_ts_ms INTEGER,retrieved_at_ms INTEGER NOT NULL,raw_body BLOB NOT NULL);
    CREATE INDEX IF NOT EXISTS derivative_time ON derivative_observations(instrument,data_type,source_ts_ms);
    """)
    response_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(source_responses)")}
    if "raw_body" not in response_columns:
        connection.execute("ALTER TABLE source_responses ADD COLUMN raw_body BLOB NOT NULL DEFAULT X''")
    return connection


class OfficialClient:
    def __init__(self, *, retries: int = 6, interval_seconds: float = 0.22) -> None:
        self.retries, self.interval_seconds = retries, interval_seconds

    def get(self, path: str, params: dict[str, Any]) -> tuple[list[Any], bytes, str, int]:
        url = BASE_URL + path + "?" + urlencode(params)
        for attempt in range(self.retries + 1):
            request = Request(url, headers={"User-Agent": "crypto-bot-thesis-derivatives/1.0",
                                            "Accept": "application/json"})
            try:
                with urlopen(request, timeout=30) as response:  # noqa: S310 fixed host
                    body = response.read()
                payload = json.loads(body)
                if payload.get("code") in {"50011", "50040"}:
                    raise HTTPError(url, 429, payload.get("msg", "rate limit"), {}, None)
                if payload.get("code") != "0":
                    raise RuntimeError(f"OKX {payload.get('code')}: {payload.get('msg')}")
                time.sleep(self.interval_seconds)
                return payload.get("data", []), body, url, attempt
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                retryable = not isinstance(error, HTTPError) or error.code in {429, 500, 502, 503, 504}
                if not retryable or attempt >= self.retries:
                    raise
                time.sleep(min(2 ** attempt, 12))
        raise RuntimeError("retry budget exhausted")


def _record_response(connection: sqlite3.Connection, lane: str, instrument: str,
                     url: str, body: bytes, timestamps: list[int], now_ms: int) -> str:
    digest = _sha(body)
    connection.execute("INSERT OR IGNORE INTO source_responses VALUES(?,?,?,?,?,?,?,?,?)",
                       (digest, lane, instrument, url, len(timestamps), min(timestamps) if timestamps else None,
                        max(timestamps) if timestamps else None, now_ms, body))
    return digest


def _checkpoint(connection: sqlite3.Connection, key: tuple[Any, ...], cursor: int | None,
                status: str, digest: str | None, retries: int) -> None:
    now = int(time.time() * 1000)
    connection.execute("""INSERT INTO backfill_checkpoints
      (lane,instrument,period,start_ms,end_ms,next_cursor_ms,status,pages,retries,last_response_sha256,updated_at_ms)
      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(lane,instrument,period,start_ms,end_ms) DO UPDATE SET
      next_cursor_ms=excluded.next_cursor_ms,status=excluded.status,pages=backfill_checkpoints.pages+1,
      retries=backfill_checkpoints.retries+excluded.retries,last_response_sha256=excluded.last_response_sha256,
      updated_at_ms=excluded.updated_at_ms""", (*key, cursor, status, 1, retries, digest, now))


def backfill(connection: sqlite3.Connection, client: OfficialClient, *, lane: str,
             instrument: str, start_ms: int, end_ms: int, period: str = "1H",
             max_pages: int = 100) -> dict[str, Any]:
    if lane not in {"oi", "funding"}:
        raise ValueError("lane must be oi or funding")
    if instrument not in INSTRUMENTS or start_ms >= end_ms:
        raise ValueError("unsupported instrument or invalid range")
    if not 1 <= max_pages <= 10_000:
        raise ValueError("max_pages must be between 1 and 10000")
    if end_ms - start_ms > MAX_RANGE_DAYS * 86_400_000:
        raise ValueError("range exceeds safety bound")
    if lane == "oi" and period not in OI_PERIODS:
        raise ValueError("unsupported OI period")
    period_key = period if lane == "oi" else "settled"
    key = (lane, instrument, period_key, start_ms, end_ms)
    existing = connection.execute("SELECT * FROM backfill_checkpoints WHERE lane=? AND instrument=? AND period=? AND start_ms=? AND end_ms=?", key).fetchone()
    if existing and existing["status"] in {"COMPLETE", "SOURCE_LIMIT_REACHED", "SOURCE_EXHAUSTED"}:
        return {"status": existing["status"], "resumed": True, "pages": 0, "rows_inserted": 0}
    cursor = int(existing["next_cursor_ms"]) if existing and existing["next_cursor_ms"] else end_ms
    inserted = pages = retries = 0
    while cursor >= start_ms and pages < max_pages:
        params: dict[str, Any] = {"instId": instrument, "limit": "100"}
        if lane == "oi":
            params.update({"period": period, "end": str(cursor)})
        else:
            params["after"] = str(cursor)
        rows, body, url, retry_count = client.get(ENDPOINTS[lane], params)
        now = int(time.time() * 1000); retries += retry_count
        timestamps = [int(row[0] if lane == "oi" else row["fundingTime"]) for row in rows]
        digest = _record_response(connection, lane, instrument, url, body, timestamps, now)
        page_rows = 0
        for row, ts in zip(rows, timestamps):
            if not start_ms <= ts <= end_ms or ts > now:
                continue
            if lane == "oi":
                if len(row) != 4:
                    raise ValueError("unexpected OI schema")
                value, unit = float(row[3]), "USD"
                aux = {"oi_contracts": row[1], "oi_coin": row[2], "official_period": period}
                kind = "OPEN_INTEREST_USD"
            else:
                value, unit, aux, kind = float(row["fundingRate"]), "rate", {
                    "funding_time_ms": ts, "realized_rate": row.get("realizedRate"),
                    "formula_type": row.get("formulaType"), "method": row.get("method")}, "FUNDING_RATE"
            before = connection.total_changes
            connection.execute("INSERT OR IGNORE INTO derivative_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (kind, instrument, ts, value, unit, json.dumps(aux, sort_keys=True),
                                "OKX_OFFICIAL", SOURCE_VERSION, now, digest))
            page_rows += connection.total_changes - before
        inserted += page_rows; pages += 1
        if not timestamps:
            _checkpoint(connection, key, None, "SOURCE_EXHAUSTED", digest, retry_count); connection.commit(); break
        next_cursor = min(timestamps) - 1
        reached_start = next_cursor < start_ms
        source_limited = len(rows) < 100 and not reached_start
        status = "COMPLETE" if reached_start else ("SOURCE_LIMIT_REACHED" if source_limited else "RUNNING")
        _checkpoint(connection, key, next_cursor, status, digest, retry_count)
        connection.commit()
        if reached_start or source_limited:
            break
        if next_cursor >= cursor:
            raise RuntimeError("pagination did not advance")
        cursor = next_cursor
    status = connection.execute("SELECT status FROM backfill_checkpoints WHERE lane=? AND instrument=? AND period=? AND start_ms=? AND end_ms=?", key).fetchone()[0]
    if pages >= max_pages and status == "RUNNING":
        status = "PAGE_BOUND_REACHED"
    return {"status": status, "resumed": bool(existing), "pages": pages,
            "rows_inserted": inserted, "retries": retries}


def _backfill_price(connection: sqlite3.Connection, client: OfficialClient, *,
                    price_type: str, swap_instrument: str, start_ms: int,
                    end_ms: int, max_pages: int) -> dict[str, Any]:
    if not 1 <= max_pages <= 10_000:
        raise ValueError("max_pages must be between 1 and 10000")
    source_instrument = (swap_instrument.removesuffix("-SWAP")
                         if price_type == "index" else swap_instrument)
    key = (price_type, swap_instrument, "1m", start_ms, end_ms)
    existing = connection.execute("SELECT * FROM backfill_checkpoints WHERE lane=? AND instrument=? AND period=? AND start_ms=? AND end_ms=?", key).fetchone()
    if existing and existing["status"] in {"COMPLETE", "SOURCE_LIMIT_REACHED", "SOURCE_EXHAUSTED"}:
        return {"status": existing["status"], "resumed": True, "pages": 0, "rows_inserted": 0}
    cursor = int(existing["next_cursor_ms"]) if existing and existing["next_cursor_ms"] else end_ms
    pages = inserted = retries = 0
    while cursor >= start_ms and pages < max_pages:
        rows, body, url, retry_count = client.get(ENDPOINTS[price_type], {
            "instId": source_instrument, "bar": "1m", "after": str(cursor), "limit": "100"})
        timestamps = [int(row[0]) for row in rows]; now = int(time.time() * 1000)
        digest = _record_response(connection, price_type, source_instrument, url, body, timestamps, now)
        retries += retry_count
        for row, ts in zip(rows, timestamps):
            if len(row) != 6:
                raise ValueError(f"unexpected {price_type} candle schema")
            if not start_ms <= ts <= end_ms or ts > now or str(row[5]) != "1":
                continue
            before = connection.total_changes
            connection.execute("INSERT OR IGNORE INTO source_price_observations VALUES(?,?,?,?,?,?,?,?,?)",
                               (price_type, swap_instrument, ts, float(row[4]), 1,
                                "OKX_OFFICIAL", SOURCE_VERSION, now, digest))
            inserted += connection.total_changes - before
        pages += 1
        if not timestamps:
            _checkpoint(connection, key, None, "SOURCE_EXHAUSTED", digest, retry_count); connection.commit(); break
        next_cursor = min(timestamps) - 1
        reached_start = next_cursor < start_ms
        source_limited = len(rows) < 100 and not reached_start
        status = "COMPLETE" if reached_start else ("SOURCE_LIMIT_REACHED" if source_limited else "RUNNING")
        _checkpoint(connection, key, next_cursor, status, digest, retry_count)
        connection.commit()
        if reached_start or source_limited: break
        if next_cursor >= cursor: raise RuntimeError("price pagination did not advance")
        cursor = next_cursor
    status = connection.execute("SELECT status FROM backfill_checkpoints WHERE lane=? AND instrument=? AND period=? AND start_ms=? AND end_ms=?", key).fetchone()[0]
    if pages >= max_pages and status == "RUNNING": status = "PAGE_BOUND_REACHED"
    return {"status": status, "resumed": bool(existing), "pages": pages,
            "rows_inserted": inserted, "retries": retries}


def backfill_basis(connection: sqlite3.Connection, client: OfficialClient, *,
                   instrument: str, start_ms: int, end_ms: int,
                   max_pages: int = 100) -> dict[str, Any]:
    if instrument not in INSTRUMENTS or start_ms >= end_ms:
        raise ValueError("unsupported instrument or invalid range")
    if end_ms - start_ms > MAX_RANGE_DAYS * 86_400_000:
        raise ValueError("range exceeds safety bound")
    mark = _backfill_price(connection, client, price_type="mark", swap_instrument=instrument,
                           start_ms=start_ms, end_ms=end_ms, max_pages=max_pages)
    index = _backfill_price(connection, client, price_type="index", swap_instrument=instrument,
                            start_ms=start_ms, end_ms=end_ms, max_pages=max_pages)
    # Exact timestamp intersection is deliberately stricter than an unbounded
    # as-of join and makes each 1m basis point independently auditable.
    now = int(time.time() * 1000)
    rows = connection.execute("""SELECT m.source_ts_ms,m.close,i.close,m.response_sha256,i.response_sha256
      FROM source_price_observations m JOIN source_price_observations i
      ON i.instrument=m.instrument AND i.source_ts_ms=m.source_ts_ms
      WHERE m.price_type='mark' AND i.price_type='index' AND m.instrument=?
      AND m.source_ts_ms BETWEEN ? AND ? ORDER BY m.source_ts_ms""",
      (instrument, start_ms, end_ms)).fetchall()
    inserted = 0
    for ts, mark_close, index_close, mark_sha, index_sha in rows:
        if float(index_close) <= 0: continue
        digest = hashlib.sha256(f"{mark_sha}:{index_sha}".encode()).hexdigest()
        before = connection.total_changes
        connection.execute("INSERT OR IGNORE INTO derivative_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                           ("BASIS_PCT", instrument, ts, (float(mark_close)-float(index_close))/float(index_close),
                            "ratio", json.dumps({"mark": mark_close, "index": index_close,
                            "join": "exact_timestamp"}, sort_keys=True), "OKX_OFFICIAL_DERIVED",
                            SOURCE_VERSION, now, digest))
        inserted += connection.total_changes - before
    connection.commit()
    return {"status": "COMPLETE" if mark["status"] == index["status"] == "COMPLETE" else "PARTIAL",
            "mark": mark, "index": index, "basis_rows_inserted": inserted}


def verify(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as connection:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        rows = connection.execute("SELECT data_type,instrument,COUNT(*),MIN(source_ts_ms),MAX(source_ts_ms) FROM derivative_observations GROUP BY 1,2 ORDER BY 1,2").fetchall()
        future = connection.execute("SELECT COUNT(*) FROM derivative_observations WHERE source_ts_ms>ingested_at_ms").fetchone()[0]
        duplicates = connection.execute("SELECT COUNT(*) FROM (SELECT 1 FROM derivative_observations GROUP BY data_type,instrument,source_ts_ms HAVING COUNT(*)>1)").fetchone()[0]
    return {"version": VERSION, "ok": quick == "ok" and not future and not duplicates,
            "quick_check": quick, "future_rows": future, "duplicate_keys": duplicates,
            "coverage": [{"data_type": r[0], "instrument": r[1], "rows": r[2],
                          "start_ms": r[3], "end_ms": r[4]} for r in rows]}


def snapshot(path: Path, manifest_path: Path, cutoff_ms: int) -> dict[str, Any]:
    report = verify(path)
    if not report["ok"]:
        raise ValueError("snapshot verification failed")
    with closing(sqlite3.connect(path)) as connection:
        beyond = connection.execute("SELECT COUNT(*) FROM derivative_observations WHERE source_ts_ms>?", (cutoff_ms,)).fetchone()[0]
        if beyond:
            raise ValueError("database contains observations beyond snapshot cutoff")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("VACUUM")
        connection.commit()
    sha = _file_sha(path)
    manifest = {"version": "thesis-derivatives-snapshot-manifest-v1", "source": "OKX_OFFICIAL",
                "source_version": SOURCE_VERSION, "cutoff_ms": cutoff_ms, "database_sha256": sha,
                "dataset_id": "thesis-derivatives-v1-" + sha[:24], "synthetic_rows": 0,
                "coverage": report["coverage"], "created_at_ms": int(time.time() * 1000)}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(); commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("backfill"); run.add_argument("--database", type=Path, required=True); run.add_argument("--lane", choices=("oi", "funding", "basis"), required=True); run.add_argument("--instrument", choices=sorted(INSTRUMENTS), required=True); run.add_argument("--start", required=True); run.add_argument("--end", required=True); run.add_argument("--period", choices=sorted(OI_PERIODS), default="1H"); run.add_argument("--max-pages", type=int, default=100); run.add_argument("--dry-run", action="store_true")
    check = commands.add_parser("verify"); check.add_argument("--database", type=Path, required=True)
    freeze = commands.add_parser("snapshot"); freeze.add_argument("--database", type=Path, required=True); freeze.add_argument("--manifest", type=Path, required=True); freeze.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    if args.command == "verify": result = verify(args.database)
    elif args.command == "snapshot": result = snapshot(args.database, args.manifest, utc_ms(args.cutoff))
    else:
        start_ms, end_ms = utc_ms(args.start), utc_ms(args.end)
        plan = {"version": VERSION, "lane": args.lane, "instrument": args.instrument,
                "start_ms": start_ms, "end_ms": end_ms, "period": args.period,
                "max_pages": args.max_pages, "network_requests": not args.dry_run}
        if args.dry_run: result = plan
        else:
            args.database.parent.mkdir(parents=True, exist_ok=True)
            with connect(args.database) as connection:
                if args.lane == "basis":
                    outcome = backfill_basis(connection, OfficialClient(), instrument=args.instrument,
                                             start_ms=start_ms, end_ms=end_ms, max_pages=args.max_pages)
                else:
                    outcome = backfill(connection, OfficialClient(), lane=args.lane,
                                       instrument=args.instrument, start_ms=start_ms, end_ms=end_ms,
                                       period=args.period, max_pages=args.max_pages)
                result = {**plan, **outcome}
    print(json.dumps(result, sort_keys=True))
    if args.command == "verify" and not result["ok"]: raise SystemExit(1)


if __name__ == "__main__":
    main()
