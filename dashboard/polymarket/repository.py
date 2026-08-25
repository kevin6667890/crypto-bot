"""SQLite append-only ledger for isolated Polymarket paper research."""
from __future__ import annotations

import sqlite3
import uuid
import json
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .models import canonical_json, stable_hash, utc_now

DEFAULT_DB_PATH = Path("data_cache/polymarket_research.sqlite")
_IMMUTABLE = ("universe_manifests", "universe_snapshots", "universe_market_refs", "market_snapshots", "eligibility_decisions", "evidence_snapshots", "evidence_retrieval_attempts", "forecasts", "forecast_evidence_refs", "resolutions", "scores", "llm_forecast_attempts", "cohort_runs", "cohort_market_results", "collection_runs")
UNIVERSE_MANIFEST_SCHEMA = "polymarket-universe-manifest-v2"
DATABASE_SCHEMA_VERSION = 2
DATABASE_SCHEMA_IDENTITY = "polymarket-sqlite-schema-v2"


class PolymarketRepository:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS markets (
              market_id TEXT PRIMARY KEY, slug TEXT NOT NULL, question TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, market_identity_hash TEXT NOT NULL UNIQUE,
              first_metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pm_markets_first_seen ON markets(first_seen_at,market_id);
            CREATE TABLE IF NOT EXISTS universe_manifests (
              manifest_hash TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
              encoding TEXT NOT NULL, compressed_payload BLOB NOT NULL,
              canonical_size_bytes INTEGER NOT NULL, market_count INTEGER NOT NULL,
              first_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS universe_snapshots (
              universe_snapshot_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL,
              selection_policy_version TEXT NOT NULL, selection_policy_hash TEXT NOT NULL,
              source_payload_json TEXT NOT NULL, source_payload_hash TEXT NOT NULL, universe_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS universe_market_refs (
              universe_snapshot_id TEXT NOT NULL REFERENCES universe_snapshots(universe_snapshot_id),
              market_id TEXT NOT NULL, ordinal INTEGER NOT NULL, market_payload_hash TEXT NOT NULL,
              PRIMARY KEY(universe_snapshot_id,ordinal), UNIQUE(universe_snapshot_id,market_id)
            );
            CREATE TABLE IF NOT EXISTS market_snapshots (
              snapshot_id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(market_id),
              captured_at TEXT NOT NULL, source_timestamp TEXT, resolution_rule_text TEXT NOT NULL,
              end_date TEXT, outcomes_json TEXT NOT NULL, token_mapping_json TEXT NOT NULL,
              gamma_payload_json TEXT NOT NULL, yes_orderbook_json TEXT NOT NULL, no_orderbook_json TEXT NOT NULL,
              yes_best_bid TEXT, yes_best_ask TEXT, yes_midpoint TEXT,
              no_best_bid TEXT, no_best_ask TEXT, no_midpoint TEXT,
              gamma_payload_hash TEXT NOT NULL, clob_payload_hash TEXT NOT NULL, snapshot_hash TEXT NOT NULL UNIQUE
              ,event_id TEXT, event_slug TEXT, neg_risk INTEGER NOT NULL DEFAULT 0,
              statistical_cluster_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pm_snapshots_market ON market_snapshots(market_id,captured_at);
            CREATE TABLE IF NOT EXISTS eligibility_decisions (
              decision_id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(market_id),
              market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id), evaluated_at TEXT NOT NULL,
              eligible INTEGER NOT NULL CHECK(eligible IN (0,1)), policy_version TEXT NOT NULL, policy_hash TEXT NOT NULL,
              reasons_json TEXT NOT NULL, decision_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_pm_eligibility_snapshot ON eligibility_decisions(market_snapshot_id,evaluated_at);
            CREATE TABLE IF NOT EXISTS evidence_snapshots (
              evidence_id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(market_id), captured_at TEXT NOT NULL,
              source_url TEXT NOT NULL, source_published_at TEXT, evidence_cutoff_at TEXT NOT NULL,
              content_type TEXT NOT NULL, payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL UNIQUE, lineage_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_retrieval_attempts (
              attempt_id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(market_id),
              query TEXT NOT NULL, source_url TEXT, retrieved_at TEXT NOT NULL,
              published_at TEXT, status TEXT NOT NULL,
              rejection_reason TEXT, payload_hash TEXT, attempt_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_pm_evidence_attempt_market ON evidence_retrieval_attempts(market_id,retrieved_at);
            CREATE TABLE IF NOT EXISTS forecasts (
              forecast_id TEXT PRIMARY KEY, forecast_hash TEXT NOT NULL UNIQUE,
              market_id TEXT NOT NULL REFERENCES markets(market_id), market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
              eligibility_decision_id TEXT NOT NULL REFERENCES eligibility_decisions(decision_id),
              forecasted_at TEXT NOT NULL, evidence_cutoff_at TEXT NOT NULL, evidence_root_hash TEXT NOT NULL,
              forecast_schema_version TEXT NOT NULL, producer_kind TEXT NOT NULL, producer_identity_json TEXT NOT NULL,
              config_hash TEXT NOT NULL, probability REAL NOT NULL CHECK(probability>0 AND probability<1),
              rationale TEXT NOT NULL, committed_at TEXT NOT NULL, CHECK(evidence_cutoff_at <= forecasted_at)
            );
            CREATE INDEX IF NOT EXISTS idx_pm_forecasts_market ON forecasts(market_id,committed_at);
            CREATE TABLE IF NOT EXISTS forecast_evidence_refs (
              forecast_id TEXT NOT NULL REFERENCES forecasts(forecast_id), evidence_id TEXT NOT NULL REFERENCES evidence_snapshots(evidence_id),
              ordinal INTEGER NOT NULL, ref_hash TEXT NOT NULL, PRIMARY KEY(forecast_id,ordinal), UNIQUE(forecast_id,evidence_id)
            );
            CREATE TABLE IF NOT EXISTS scores (
              score_id TEXT PRIMARY KEY, forecast_id TEXT NOT NULL REFERENCES forecasts(forecast_id),
              resolution_identity TEXT NOT NULL, scored_at TEXT NOT NULL, forecast_probability REAL NOT NULL,
              market_midpoint_probability REAL NOT NULL, outcome_value INTEGER NOT NULL CHECK(outcome_value IN (0,1)),
              forecast_brier REAL NOT NULL, market_brier REAL NOT NULL, forecast_log_loss REAL NOT NULL, market_log_loss REAL NOT NULL,
              scoring_version TEXT NOT NULL, UNIQUE(forecast_id,resolution_identity,scoring_version)
            );
            CREATE TABLE IF NOT EXISTS resolutions (
              resolution_id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(market_id), revision INTEGER NOT NULL,
              observed_at TEXT NOT NULL, resolved_at TEXT, classification TEXT NOT NULL,
              outcome_value INTEGER CHECK(outcome_value IN (0,1)), source_payload_json TEXT NOT NULL,
              source_hash TEXT NOT NULL, supersedes_resolution_id TEXT REFERENCES resolutions(resolution_id),
              UNIQUE(market_id,revision), UNIQUE(market_id,source_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_pm_resolutions_market ON resolutions(market_id,revision);
            CREATE TABLE IF NOT EXISTS llm_forecast_attempts (
              attempt_id TEXT PRIMARY KEY, market_id TEXT NOT NULL REFERENCES markets(market_id),
              market_snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
              eligibility_decision_id TEXT NOT NULL REFERENCES eligibility_decisions(decision_id),
              attempted_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','FAILED')),
              failure_code TEXT, provider_identity_json TEXT NOT NULL, generation_config_json TEXT NOT NULL,
              prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL, evidence_root_hash TEXT NOT NULL,
              request_hash TEXT NOT NULL, raw_response TEXT, raw_response_hash TEXT, attempt_hash TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_pm_llm_attempt_market ON llm_forecast_attempts(market_id,attempted_at);
            CREATE TABLE IF NOT EXISTS cohort_runs (
              cohort_id TEXT PRIMARY KEY, universe_snapshot_id TEXT NOT NULL REFERENCES universe_snapshots(universe_snapshot_id),
              started_at TEXT NOT NULL, completed_at TEXT NOT NULL, status TEXT NOT NULL,
              eligibility_policy_version TEXT NOT NULL, eligibility_policy_hash TEXT NOT NULL,
              evidence_policy_version TEXT NOT NULL, evidence_policy_hash TEXT NOT NULL,
              forecast_schema_version TEXT NOT NULL, prompt_version TEXT NOT NULL, prompt_hash TEXT NOT NULL,
              provider_policy_version TEXT NOT NULL, provider_policy_hash TEXT NOT NULL,
              model_identity_json TEXT NOT NULL, scoring_version TEXT NOT NULL,
              execution_simulation_version TEXT NOT NULL, config_json TEXT NOT NULL,
              cohort_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS cohort_market_results (
              cohort_id TEXT NOT NULL REFERENCES cohort_runs(cohort_id), ordinal INTEGER NOT NULL,
              market_id TEXT NOT NULL, market_snapshot_id TEXT, eligibility_decision_id TEXT,
              status TEXT NOT NULL, forecast_id TEXT REFERENCES forecasts(forecast_id),
              detail_json TEXT NOT NULL, result_hash TEXT NOT NULL UNIQUE,
              PRIMARY KEY(cohort_id,ordinal), UNIQUE(cohort_id,market_id)
            );
            CREATE TABLE IF NOT EXISTS collection_runs (
              collection_run_id TEXT PRIMARY KEY, cohort_id TEXT REFERENCES cohort_runs(cohort_id),
              started_at TEXT NOT NULL, completed_at TEXT NOT NULL, status TEXT NOT NULL,
              summary_json TEXT NOT NULL, error_code TEXT, run_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS collection_leases (
              lease_name TEXT PRIMARY KEY, collection_run_id TEXT NOT NULL,
              acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
              host_identity TEXT NOT NULL, process_identity TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collection_observations (
              observation_id TEXT PRIMARY KEY, collection_run_id TEXT,
              market_id TEXT NOT NULL, observed_at TEXT NOT NULL,
              market_metadata_fingerprint TEXT NOT NULL, observation_kind TEXT NOT NULL,
              detail_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pm_observation_market ON collection_observations(market_id,observed_at);
            """)
            # Backward-compatible extensions for databases created by Phase 1A.
            for column in ("resolution_id TEXT", "brier_delta REAL", "log_loss_delta REAL", "executable_side TEXT", "executable_entry_ask REAL", "executable_gross_pnl REAL", "executable_net_pnl REAL", "fee_status TEXT", "fee_model_version TEXT"):
                try:
                    c.execute(f"ALTER TABLE scores ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            # Event identity is point-in-time lineage: never mutate an old
            # snapshot merely because Gamma later changes its event metadata.
            for column in ("event_id TEXT", "event_slug TEXT", "neg_risk INTEGER NOT NULL DEFAULT 0", "statistical_cluster_id TEXT"):
                try:
                    c.execute(f"ALTER TABLE market_snapshots ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            for column in ("pagination_policy_version TEXT", "pagination_policy_hash TEXT", "market_count INTEGER", "pagination_metadata_json TEXT"):
                try:
                    c.execute(f"ALTER TABLE universe_snapshots ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            try:
                c.execute("ALTER TABLE universe_snapshots ADD COLUMN manifest_hash TEXT")
            except sqlite3.OperationalError:
                pass
            for column in ("cohort_id TEXT", "forecast_methodology_hash TEXT"):
                try:
                    c.execute(f"ALTER TABLE forecasts ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            for column in ("contracts REAL", "estimated_fee REAL", "execution_simulation_version TEXT", "execution_policy_hash TEXT", "minimum_edge REAL"):
                try:
                    c.execute(f"ALTER TABLE scores ADD COLUMN {column}")
                except sqlite3.OperationalError:
                    pass
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pm_initial_llm_methodology ON forecasts(market_id,forecast_methodology_hash) WHERE producer_kind='LLM' AND forecast_methodology_hash IS NOT NULL")
            # Read-model projections: compact indexes only, never raw JSON.
            c.execute("CREATE INDEX IF NOT EXISTS idx_pm_eligibility_market_evaluated ON eligibility_decisions(market_id,evaluated_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pm_forecasts_kind_committed ON forecasts(producer_kind,committed_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pm_scores_forecast_scored ON scores(forecast_id,scored_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pm_snapshots_event ON market_snapshots(event_slug,statistical_cluster_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pm_forecasts_committed ON forecasts(committed_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pm_scores_forecast ON scores(forecast_id)")
            for table in _IMMUTABLE:
                c.execute(f"CREATE TRIGGER IF NOT EXISTS pm_{table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")
                c.execute(f"CREATE TRIGGER IF NOT EXISTS pm_{table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")
            c.execute(f"PRAGMA user_version={DATABASE_SCHEMA_VERSION}")

    def latest_metadata_state(self, market_id: str) -> dict[str, Any] | None:
        """Latest frozen metadata/decision, used only to decide collection I/O."""
        with self.connect() as c:
            row = c.execute("""SELECT s.gamma_payload_hash,s.gamma_payload_json,s.captured_at,s.snapshot_id,d.decision_id,d.eligible,d.reasons_json,
                EXISTS(SELECT 1 FROM forecasts f WHERE f.market_id=s.market_id AND f.producer_kind='LLM') AS forecasted
                FROM market_snapshots s JOIN eligibility_decisions d ON d.market_snapshot_id=s.snapshot_id
                WHERE s.market_id=? ORDER BY s.captured_at DESC LIMIT 1""", (market_id,)).fetchone()
        if not row:
            return None
        result = dict(row); result["reasons"] = json.loads(result.pop("reasons_json")); result["gamma_payload"] = json.loads(result.pop("gamma_payload_json"))
        return result

    def record_observations(self, collection_run_id: str | None, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        with self.connect() as c:
            c.executemany("INSERT INTO collection_observations VALUES(?,?,?,?,?,?,?)", [
                (str(uuid.uuid4()), collection_run_id, row["market_id"], row["observed_at"], row["market_metadata_fingerprint"], row["observation_kind"], canonical_json(row.get("detail", {}))) for row in rows])

    def acquire_collection_lease(self, *, collection_run_id: str, host_identity: str, process_identity: str,
                                 stale_after_seconds: int = 7200) -> bool:
        """Acquire a SQLite transactional lease; stale owners can be replaced safely."""
        from datetime import datetime, timezone
        now = utc_now()
        with self.connect() as c:
            row = c.execute("SELECT * FROM collection_leases WHERE lease_name='polymarket-collector'").fetchone()
            if row:
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["heartbeat_at"])).total_seconds()
                except ValueError:
                    age = float("inf")
                if age <= stale_after_seconds:
                    return False
                c.execute("DELETE FROM collection_leases WHERE lease_name='polymarket-collector'")
            c.execute("INSERT INTO collection_leases VALUES(?,?,?,?,?,?)", ("polymarket-collector", collection_run_id, now, now, host_identity, process_identity))
        return True

    def heartbeat_collection_lease(self, collection_run_id: str) -> bool:
        with self.connect() as c:
            updated = c.execute("UPDATE collection_leases SET heartbeat_at=? WHERE lease_name='polymarket-collector' AND collection_run_id=?", (utc_now(), collection_run_id)).rowcount
        return bool(updated)

    def release_collection_lease(self, collection_run_id: str) -> None:
        with self.connect() as c:
            c.execute("DELETE FROM collection_leases WHERE lease_name='polymarket-collector' AND collection_run_id=?", (collection_run_id,))

    def collection_lease(self) -> dict[str, Any] | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM collection_leases WHERE lease_name='polymarket-collector'").fetchone()
        return dict(row) if row else None

    def storage_status(self) -> dict[str, Any]:
        db_size = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_name(self.path.name + '-wal')
        with self.connect() as c:
            tables = [str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            counts = {t: int(c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}
            page_size = int(c.execute('PRAGMA page_size').fetchone()[0])
            try:
                sizes = {str(r['name']): int(r['bytes'] or 0) for r in c.execute("SELECT name, SUM(pgsize) bytes FROM dbstat GROUP BY name").fetchall()}
            except sqlite3.OperationalError:
                # Some Windows SQLite builds omit SQLITE_ENABLE_DBSTAT_VTAB.
                # Estimate payload bytes from a bounded rowid sample rather
                # than scanning the multi-GB file; label it honestly.
                payload_columns = {
                    'markets': ('first_metadata_json',), 'universe_snapshots': ('source_payload_json', 'pagination_metadata_json'),
                    'universe_manifests': ('compressed_payload',),
                    'market_snapshots': ('gamma_payload_json', 'yes_orderbook_json', 'no_orderbook_json'),
                    'evidence_snapshots': ('payload_json', 'lineage_json'), 'llm_forecast_attempts': ('raw_response',),
                    'collection_runs': ('summary_json',), 'cohort_runs': ('config_json',), 'cohort_market_results': ('detail_json',),
                }
                sizes = {}
                for table, columns in payload_columns.items():
                    if not counts.get(table):
                        sizes[table] = 0; continue
                    expression = '+'.join(f'COALESCE(LENGTH("{column}"),0)' for column in columns)
                    sample = c.execute(f'SELECT COUNT(*) n, AVG(payload_bytes) mean_bytes FROM (SELECT {expression} AS payload_bytes FROM "{table}" WHERE rowid IS NOT NULL LIMIT 256)').fetchone()
                    sizes[table] = int((float(sample['mean_bytes'] or 0)) * counts[table])
            raw = c.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT gamma_payload_hash) AS d FROM market_snapshots").fetchone()
            manifests = c.execute("SELECT COUNT(*) n,COALESCE(SUM(canonical_size_bytes),0) canonical_bytes,COALESCE(SUM(LENGTH(compressed_payload)),0) compressed_bytes,COALESCE(SUM(market_count),0) represented_markets FROM universe_manifests").fetchone()
            compact_snapshots = int(c.execute("SELECT COUNT(*) FROM universe_snapshots WHERE manifest_hash IS NOT NULL").fetchone()[0])
            latest = c.execute("SELECT summary_json FROM collection_runs ORDER BY completed_at DESC LIMIT 1").fetchone()
        return {"db_path": str(self.path), "db_size_bytes": db_size, "wal_size_bytes": wal.stat().st_size if wal.exists() else 0,
                "table_counts": counts, "table_size_estimate_bytes": sizes, "page_size": page_size,
                "market_snapshot_payloads": {"count": int(raw['n']), "distinct_gamma_hashes": int(raw['d']), "dedup_ratio": (1 - int(raw['d']) / int(raw['n'])) if raw['n'] else 0},
                "universe_manifest_storage": {"schema": UNIVERSE_MANIFEST_SCHEMA, "count": int(manifests['n']),
                    "canonical_bytes": int(manifests['canonical_bytes']), "compressed_bytes": int(manifests['compressed_bytes']),
                    "represented_markets": int(manifests['represented_markets']), "compact_universe_snapshots": compact_snapshots},
                "latest_collection": json.loads(latest['summary_json']) if latest else None}

    def universe_manifest(self, universe_snapshot_id: str) -> list[dict[str, str]]:
        """Reconstruct the exact market-id/payload-hash set seen by a run.

        Storage-v1 snapshots are reconstructed from their immutable ref rows;
        storage-v2 snapshots use a checksummed compressed canonical manifest.
        """
        with self.connect() as c:
            snapshot = c.execute("SELECT manifest_hash,market_count FROM universe_snapshots WHERE universe_snapshot_id=?", (universe_snapshot_id,)).fetchone()
            if not snapshot:
                raise KeyError(universe_snapshot_id)
            if not snapshot["manifest_hash"]:
                return [{"market_id": str(row[0]), "payload_hash": str(row[1])} for row in c.execute(
                    "SELECT market_id,market_payload_hash FROM universe_market_refs WHERE universe_snapshot_id=? ORDER BY ordinal", (universe_snapshot_id,))]
            row = c.execute("SELECT * FROM universe_manifests WHERE manifest_hash=?", (snapshot["manifest_hash"],)).fetchone()
        if not row or row["encoding"] != "zlib-json":
            raise ValueError("universe manifest unavailable or unsupported")
        canonical = zlib.decompress(row["compressed_payload"])
        if len(canonical) != int(row["canonical_size_bytes"]):
            raise ValueError("universe manifest size mismatch")
        document = json.loads(canonical)
        pairs = document.get("markets") if isinstance(document, dict) else None
        if not isinstance(document, dict) or document.get("schema") != UNIVERSE_MANIFEST_SCHEMA or not isinstance(pairs, list):
            raise ValueError("universe manifest schema mismatch")
        payload = [{"market_id": str(pair[0]), "payload_hash": str(pair[1])} for pair in pairs]
        expected_hash = stable_hash({"schema": UNIVERSE_MANIFEST_SCHEMA, "markets": pairs})
        if expected_hash != row["manifest_hash"] or len(payload) != int(snapshot["market_count"] or 0):
            raise ValueError("universe manifest integrity mismatch")
        return payload

    def database_file_status(self) -> dict[str, int]:
        """O(1) filesystem health information; intentionally no ledger scans."""
        wal = self.path.with_name(self.path.name + '-wal')
        return {'db_size_bytes': self.path.stat().st_size if self.path.exists() else 0,
                'wal_size_bytes': wal.stat().st_size if wal.exists() else 0}

    def _persist_snapshot(self, c: sqlite3.Connection, snapshot: dict[str, Any], eligibility: dict[str, Any]) -> tuple[str, str]:
        market = snapshot["market"]
        market_id = str(market["id"])
        captured = str(snapshot["captured_at"])
        identity = stable_hash({"market_id": market_id, "slug": market.get("slug"), "question": market.get("question")})
        gamma_json = canonical_json(snapshot["gamma_payload"])
        yes_json, no_json = canonical_json(snapshot["yes_orderbook"]), canonical_json(snapshot["no_orderbook"])
        gamma_hash = str(snapshot.get("gamma_payload_hash_override") or stable_hash(snapshot["gamma_payload"]))
        clob_hash = stable_hash({"YES": snapshot["yes_orderbook"], "NO": snapshot["no_orderbook"]})
        lineage = snapshot.get("event_lineage") if isinstance(snapshot.get("event_lineage"), dict) else {}
        event_id = lineage.get("event_id")
        event_slug = lineage.get("event_slug")
        neg_risk = int(bool(lineage.get("neg_risk")))
        cluster_id = lineage.get("statistical_cluster_id") or str(event_id or market_id)
        frozen = {"market_id": market_id, "captured_at": captured, "source_timestamp": snapshot.get("source_timestamp"), "resolution_rule_text": snapshot["resolution_rule_text"], "end_date": snapshot.get("end_date"), "outcomes": snapshot["outcomes"], "token_mapping": snapshot["token_mapping"], "gamma_hash": gamma_hash, "clob_hash": clob_hash, "quotes": snapshot["quotes"], "event_id": event_id, "event_slug": event_slug, "neg_risk": neg_risk, "statistical_cluster_id": cluster_id}
        snapshot_hash = stable_hash(frozen)
        snapshot_id = str(uuid.uuid4())
        decision_id = str(uuid.uuid4())
        decision_hash = stable_hash({"market_snapshot_hash": snapshot_hash, "eligible": eligibility["eligible"], "policy_version": eligibility["policy_version"], "policy_hash": eligibility["policy_hash"], "reasons": eligibility["reasons"]})
        c.execute("INSERT OR IGNORE INTO markets(market_id,slug,question,first_seen_at,market_identity_hash,first_metadata_json) VALUES(?,?,?,?,?,?)", (market_id, str(market.get("slug") or ""), str(market.get("question") or ""), captured, identity, gamma_json))
        c.execute("""INSERT INTO market_snapshots(
                snapshot_id,market_id,captured_at,source_timestamp,resolution_rule_text,end_date,outcomes_json,token_mapping_json,
                gamma_payload_json,yes_orderbook_json,no_orderbook_json,yes_best_bid,yes_best_ask,yes_midpoint,
                no_best_bid,no_best_ask,no_midpoint,gamma_payload_hash,clob_payload_hash,snapshot_hash,
                event_id,event_slug,neg_risk,statistical_cluster_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, market_id, captured, snapshot.get("source_timestamp"), snapshot["resolution_rule_text"], snapshot.get("end_date"), canonical_json(snapshot["outcomes"]), canonical_json(snapshot["token_mapping"]), gamma_json, yes_json, no_json, snapshot["quotes"]["YES"].get("best_bid"), snapshot["quotes"]["YES"].get("best_ask"), snapshot["quotes"]["YES"].get("midpoint"), snapshot["quotes"]["NO"].get("best_bid"), snapshot["quotes"]["NO"].get("best_ask"), snapshot["quotes"]["NO"].get("midpoint"), gamma_hash, clob_hash, snapshot_hash, event_id, event_slug, neg_risk, cluster_id))
        c.execute("INSERT INTO eligibility_decisions VALUES(?,?,?,?,?,?,?,?,?)", (decision_id, market_id, snapshot_id, captured, int(eligibility["eligible"]), eligibility["policy_version"], eligibility["policy_hash"], canonical_json(eligibility["reasons"]), decision_hash))
        return snapshot_id, decision_id

    def persist_snapshot(self, snapshot: dict[str, Any], eligibility: dict[str, Any]) -> tuple[str, str]:
        with self.connect() as c:
            return self._persist_snapshot(c, snapshot, eligibility)

    def persist_snapshots(self, values: Sequence[tuple[dict[str, Any], dict[str, Any]]]) -> list[tuple[str, str]]:
        """Persist a whole universe in one transaction without changing ordering."""
        with self.connect() as c:
            return [self._persist_snapshot(c, snapshot, eligibility) for snapshot, eligibility in values]

    def persist_universe(self, markets: Sequence[dict[str, Any]], selection_policy_version: str, selection_policy_hash: str, captured_at: str | None = None, *, pagination_metadata: dict[str, Any] | None = None, market_payload_hashes: dict[str, str] | None = None) -> str:
        """Freeze all considered market payloads in canonical ID order."""
        captured_at = captured_at or utc_now()
        ordered = sorted(markets, key=lambda market: str(market.get("id", "")))
        if any(not market.get("id") for market in ordered) or len({str(m["id"]) for m in ordered}) != len(ordered):
            raise ValueError("universe has missing or duplicate market ids")
        # The immutable per-market snapshot stores the complete Gamma payload.
        # The universe ledger stores the canonical refs/hashes, avoiding a
        # second full copy of a very large active universe on every collection.
        hashes = market_payload_hashes or {}
        payload = [{"market_id": str(m["id"]), "payload_hash": hashes.get(str(m["id"])) or stable_hash(m)} for m in ordered]
        source_hash = stable_hash(payload)
        pairs = [[item["market_id"], item["payload_hash"]] for item in payload]
        manifest_document = {"schema": UNIVERSE_MANIFEST_SCHEMA, "markets": pairs}
        manifest_hash = stable_hash(manifest_document)
        manifest_bytes = canonical_json(manifest_document).encode("utf-8")
        compressed_manifest = zlib.compress(manifest_bytes, level=9)
        pagination_metadata = dict(pagination_metadata or {})
        pagination_version = str(pagination_metadata.get("version") or "gamma-offset-pagination-v1")
        pagination_hash = stable_hash({"version": pagination_version, "metadata": pagination_metadata})
        universe_hash = stable_hash({"captured_at": captured_at, "selection_policy_version": selection_policy_version, "selection_policy_hash": selection_policy_hash, "pagination_policy_hash": pagination_hash, "payload_hash": source_hash})
        universe_id = str(uuid.uuid4())
        with self.connect() as c:
            c.execute("""INSERT OR IGNORE INTO universe_manifests(
                         manifest_hash,schema_version,encoding,compressed_payload,canonical_size_bytes,market_count,first_seen_at)
                         VALUES(?,?,?,?,?,?,?)""", (manifest_hash, UNIVERSE_MANIFEST_SCHEMA, "zlib-json", compressed_manifest,
                                                   len(manifest_bytes), len(payload), captured_at))
            c.execute("""INSERT INTO universe_snapshots(universe_snapshot_id,captured_at,selection_policy_version,selection_policy_hash,source_payload_json,source_payload_hash,universe_hash,pagination_policy_version,pagination_policy_hash,market_count,pagination_metadata_json,manifest_hash)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (universe_id, captured_at, selection_policy_version, selection_policy_hash,
                         canonical_json({"storage": UNIVERSE_MANIFEST_SCHEMA, "manifest_hash": manifest_hash}), source_hash,
                         universe_hash, pagination_version, pagination_hash, len(ordered), canonical_json(pagination_metadata), manifest_hash))
            # Future runs retain the exact complete universe in one compressed,
            # content-addressed immutable object.  Historical verbose refs are
            # deliberately untouched and remain reconstructable above.
        return universe_id

    def insert_cohort(self, values: dict[str, Any], market_results: Sequence[dict[str, Any]]) -> str:
        cohort_id = str(values.get("cohort_id") or uuid.uuid4())
        identity = {key: values[key] for key in ("universe_snapshot_id", "started_at", "completed_at", "status", "eligibility_policy_version", "eligibility_policy_hash", "evidence_policy_version", "evidence_policy_hash", "forecast_schema_version", "prompt_version", "prompt_hash", "provider_policy_version", "provider_policy_hash", "model_identity", "scoring_version", "execution_simulation_version", "config")}
        with self.connect() as c:
            c.execute("INSERT INTO cohort_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (cohort_id, values["universe_snapshot_id"], values["started_at"], values["completed_at"], values["status"], values["eligibility_policy_version"], values["eligibility_policy_hash"], values["evidence_policy_version"], values["evidence_policy_hash"], values["forecast_schema_version"], values["prompt_version"], values["prompt_hash"], values["provider_policy_version"], values["provider_policy_hash"], canonical_json(values["model_identity"]), values["scoring_version"], values["execution_simulation_version"], canonical_json(values["config"]), stable_hash(identity)))
            for ordinal, result in enumerate(market_results):
                detail = dict(result.get("detail") or {})
                result_hash = stable_hash({"cohort_id": cohort_id, "ordinal": ordinal, "market_id": result["market_id"], "snapshot_id": result.get("market_snapshot_id"), "decision_id": result.get("eligibility_decision_id"), "status": result["status"], "forecast_id": result.get("forecast_id"), "detail": detail})
                c.execute("INSERT INTO cohort_market_results VALUES(?,?,?,?,?,?,?,?,?)", (cohort_id, ordinal, result["market_id"], result.get("market_snapshot_id"), result.get("eligibility_decision_id"), result["status"], result.get("forecast_id"), canonical_json(detail), result_hash))
        return cohort_id

    def insert_collection_run(self, values: dict[str, Any]) -> str:
        run_id = str(values.get("collection_run_id") or uuid.uuid4())
        identity = {"cohort_id": values.get("cohort_id"), "started_at": values["started_at"], "completed_at": values["completed_at"], "status": values["status"], "summary": values["summary"], "error_code": values.get("error_code")}
        with self.connect() as c:
            c.execute("INSERT INTO collection_runs VALUES(?,?,?,?,?,?,?,?)", (run_id, values.get("cohort_id"), values["started_at"], values["completed_at"], values["status"], canonical_json(values["summary"]), values.get("error_code"), stable_hash(identity)))
        return run_id

    def has_llm_forecast(self, market_id: str, methodology_hash: str | None = None) -> bool:
        with self.connect() as c:
            if methodology_hash:
                row = c.execute("SELECT 1 FROM forecasts WHERE market_id=? AND producer_kind='LLM' AND (forecast_methodology_hash=? OR forecast_methodology_hash IS NULL) LIMIT 1", (market_id, methodology_hash)).fetchone()
            else:
                row = c.execute("SELECT 1 FROM forecasts WHERE market_id=? AND producer_kind='LLM' LIMIT 1", (market_id,)).fetchone()
        return row is not None

    def append_resolution(self, market_id: str, payload: dict[str, Any], *, classification: str, outcome: int | None, resolved_at: str | None) -> dict[str, Any]:
        source_hash, observed_at = stable_hash(payload), utc_now()
        with self.connect() as c:
            prior_same = c.execute("SELECT * FROM resolutions WHERE market_id=? AND source_hash=?", (market_id, source_hash)).fetchone()
            if prior_same:
                return dict(prior_same)
            last = c.execute("SELECT * FROM resolutions WHERE market_id=? ORDER BY revision DESC LIMIT 1", (market_id,)).fetchone()
            # An unresolved watcher heartbeat is useful once, not on every
            # dynamic Gamma price update. Terminal/corrected source payloads
            # still create immutable revisions.
            if last and classification == "UNRESOLVED" and last["classification"] == "UNRESOLVED":
                return dict(last)
            resolution_id = str(uuid.uuid4())
            revision = (int(last["revision"]) + 1) if last else 1
            c.execute("INSERT INTO resolutions VALUES(?,?,?,?,?,?,?,?,?,?)", (resolution_id, market_id, revision, observed_at, resolved_at, classification, outcome, canonical_json(payload), source_hash, last["resolution_id"] if last else None))
            return {"resolution_id": resolution_id, "market_id": market_id, "revision": revision, "classification": classification, "outcome_value": outcome}

    def latest_resolution(self, market_id: str) -> sqlite3.Row | None:
        with self.connect() as c:
            return c.execute("SELECT * FROM resolutions WHERE market_id=? ORDER BY revision DESC LIMIT 1", (market_id,)).fetchone()

    def score_resolved_forecasts(self, *, fee_model_version: str = "UNKNOWN", contracts: float = 1.0,
                                 minimum_edge: float = 0.05, estimated_fee: float | None = None) -> int:
        from .scoring import SCORING_VERSION, executable_pnl_v1, paired_scores
        inserted = 0
        with self.connect() as c:
            rows = c.execute("""SELECT f.*, r.resolution_id,r.outcome_value,s.yes_midpoint,s.yes_best_ask,s.no_best_ask
                FROM forecasts f JOIN resolutions r ON r.market_id=f.market_id
                JOIN market_snapshots s ON s.snapshot_id=f.market_snapshot_id
                WHERE f.producer_kind='LLM'
                AND r.revision=(SELECT MAX(r2.revision) FROM resolutions r2 WHERE r2.market_id=r.market_id)
                AND r.classification='VALID_BINARY' AND NOT EXISTS
                (SELECT 1 FROM scores x WHERE x.forecast_id=f.forecast_id AND x.resolution_identity=r.resolution_id AND x.scoring_version=?)""", (SCORING_VERSION,)).fetchall()
            for row in rows:
                if row["yes_midpoint"] is None:
                    continue
                metrics = paired_scores(float(row["probability"]), float(row["yes_midpoint"]), int(row["outcome_value"]))
                pnl = executable_pnl_v1(float(row["probability"]), float(row["yes_midpoint"]), float(row["yes_best_ask"]) if row["yes_best_ask"] else None, float(row["no_best_ask"]) if row["no_best_ask"] else None, int(row["outcome_value"]), contracts=contracts, minimum_edge=minimum_edge, fee_model_version=fee_model_version, estimated_fee=estimated_fee)
                c.execute("""INSERT INTO scores(score_id,forecast_id,resolution_identity,scored_at,forecast_probability,market_midpoint_probability,outcome_value,forecast_brier,market_brier,forecast_log_loss,market_log_loss,scoring_version,resolution_id,brier_delta,log_loss_delta,executable_side,executable_entry_ask,executable_gross_pnl,executable_net_pnl,fee_status,fee_model_version,contracts,estimated_fee,execution_simulation_version,execution_policy_hash,minimum_edge)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), row["forecast_id"], row["resolution_id"], utc_now(), row["probability"], row["yes_midpoint"], row["outcome_value"], metrics["forecast_brier"], metrics["market_brier"], metrics["forecast_log_loss"], metrics["market_log_loss"], SCORING_VERSION, row["resolution_id"], metrics["brier_delta"], metrics["log_loss_delta"], pnl["side"], pnl["entry_ask"], pnl["gross_pnl"], pnl["net_pnl"], pnl["fee_status"], fee_model_version, pnl["contracts"], pnl["estimated_fee"], pnl["execution_policy_version"], pnl["execution_policy_hash"], pnl["minimum_edge"]))
                inserted += 1
        return inserted

    def status(self) -> dict[str, int]:
        with self.connect() as c:
            scalar = lambda sql: int(c.execute(sql).fetchone()[0])
            return {"active_universe_count": scalar("SELECT COALESCE(market_count,(SELECT COUNT(*) FROM universe_market_refs r WHERE r.universe_snapshot_id=u.universe_snapshot_id)) FROM universe_snapshots u ORDER BY captured_at DESC LIMIT 1"), "eligible_count": scalar("SELECT COUNT(*) FROM eligibility_decisions WHERE eligible=1"), "forecasts_count": scalar("SELECT COUNT(*) FROM forecasts"), "unresolved": scalar("SELECT COUNT(*) FROM forecasts f WHERE NOT EXISTS (SELECT 1 FROM resolutions r WHERE r.market_id=f.market_id AND r.classification='VALID_BINARY')"), "resolved": scalar("SELECT COUNT(DISTINCT market_id) FROM resolutions WHERE classification='VALID_BINARY'"), "scored": scalar("SELECT COUNT(*) FROM scores"), "rejected_or_ambiguous_resolution": scalar("SELECT COUNT(*) FROM resolutions WHERE classification!='VALID_BINARY'")}

    def forecast_market_ids(self) -> list[str]:
        with self.connect() as c:
            return [str(row[0]) for row in c.execute("SELECT DISTINCT market_id FROM forecasts ORDER BY market_id")]

    def research_statistics(self) -> dict[str, Any]:
        from .statistics import statistics_from_connection
        with self.connect() as c:
            return statistics_from_connection(c)

    def operational_status(self) -> dict[str, Any]:
        with self.connect() as c:
            latest_universe = c.execute("SELECT universe_snapshot_id,captured_at,market_count FROM universe_snapshots ORDER BY captured_at DESC LIMIT 1").fetchone()
            latest_collection = c.execute("SELECT * FROM collection_runs ORDER BY completed_at DESC LIMIT 1").fetchone()
            latest_success = c.execute("SELECT * FROM collection_runs WHERE status='SUCCEEDED' ORDER BY completed_at DESC LIMIT 1").fetchone()
            latest_summary = __import__("json").loads(latest_collection["summary_json"]) if latest_collection else {}
            latest_pipeline = latest_summary.get("pipeline", {}) if isinstance(latest_summary, dict) else {}
            scalar = lambda sql, params=(): int(c.execute(sql, params).fetchone()[0])
            markets = {
                "latest_universe_id": latest_universe["universe_snapshot_id"] if latest_universe else None,
                "latest_universe_captured_at": latest_universe["captured_at"] if latest_universe else None,
                "latest_universe_count": int(latest_universe["market_count"] or 0) if latest_universe else 0,
                "metadata_candidates": int(latest_pipeline.get("metadata_candidate_count", 0)),
                "eligible": int(latest_summary.get("eligible_count", latest_pipeline.get("eligible_count", 0))),
            }
            forecasts = {
                "LLM": scalar("SELECT COUNT(*) FROM forecasts WHERE producer_kind='LLM'"),
                "manual": scalar("SELECT COUNT(*) FROM forecasts WHERE producer_kind IN ('MANUAL','MOCK')"),
                "failed_attempts": scalar("SELECT COUNT(*) FROM llm_forecast_attempts WHERE status='FAILED'"),
                "unresolved": scalar("SELECT COUNT(*) FROM forecasts f WHERE f.producer_kind='LLM' AND COALESCE((SELECT r.classification FROM resolutions r WHERE r.market_id=f.market_id ORDER BY r.revision DESC LIMIT 1),'UNRESOLVED')!='VALID_BINARY'"),
                "resolved": scalar("SELECT COUNT(*) FROM forecasts f WHERE f.producer_kind='LLM' AND (SELECT r.classification FROM resolutions r WHERE r.market_id=f.market_id ORDER BY r.revision DESC LIMIT 1)='VALID_BINARY'"),
            }
            evidence = {"accepted": scalar("SELECT COUNT(*) FROM evidence_retrieval_attempts WHERE status='ACCEPTED'"), "rejected": scalar("SELECT COUNT(*) FROM evidence_retrieval_attempts WHERE status='REJECTED'")}
            duration_seconds = None
            if latest_collection:
                from datetime import datetime
                duration_seconds = max(0.0, (datetime.fromisoformat(latest_collection["completed_at"]) - datetime.fromisoformat(latest_collection["started_at"])).total_seconds())
            system = {"latest_successful_collection": latest_success["completed_at"] if latest_success else None,
                      "latest_collection_status": latest_collection["status"] if latest_collection else None,
                      "latest_collection_duration_seconds": duration_seconds,
                      "latest_error": latest_collection["error_code"] if latest_collection else None}
        stats = self.research_statistics()
        return {"markets": markets, "forecasts": forecasts, "evidence": evidence,
                "scoring": {key: stats[key] for key in ("scored_count", "mean_delta_brier", "mean_delta_logloss")},
                "paper": {key: stats[key] for key in ("trade_count", "no_trade_count", "known_fee_net_pnl", "unknown_fee_count")},
                "system": system, "cohort_statistics": stats}

    def latest_eligible(self, market_id: str) -> sqlite3.Row | None:
        with self.connect() as c:
            return c.execute("SELECT f.* FROM eligibility_decisions f JOIN market_snapshots s ON s.snapshot_id=f.market_snapshot_id WHERE f.market_id=? AND f.eligible=1 ORDER BY f.evaluated_at DESC LIMIT 1", (market_id,)).fetchone()

    def insert_evidence(self, market_id: str, payload: dict[str, Any], source_url: str = "local://manual", cutoff_at: str | None = None) -> str:
        evidence_id, captured = str(uuid.uuid4()), utc_now()
        canonical = canonical_json(payload)
        # Structured evidence v1 carries source metadata in its frozen payload.  Keep
        # the indexed columns aligned without inventing a publication timestamp.
        effective_url = str(payload.get("source_url") or source_url)
        published_at = payload.get("published_at")
        source_published_at = str(published_at) if published_at else None
        lineage = {"source": "manual", "title": payload.get("title"), "source_type": payload.get("source_type"), "timestamp_status": payload.get("timestamp_status", "timestamp_unknown")}
        with self.connect() as c:
            c.execute("INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)", (evidence_id, market_id, captured, effective_url, source_published_at, cutoff_at or captured, "application/json", canonical, stable_hash(payload), canonical_json(lineage)))
        return evidence_id

    def insert_evidence_attempt(self, *, market_id: str, query: str, source_url: str | None,
                                retrieved_at: str, published_at: str | None, status: str,
                                rejection_reason: str | None, payload_hash: str | None) -> str:
        """Append every considered public source; rejected sources are audit data too."""
        attempt_id = str(uuid.uuid4())
        identity = {
            "market_id": market_id, "query": query, "source_url": source_url,
            "retrieved_at": retrieved_at, "published_at": published_at, "status": status,
            "rejection_reason": rejection_reason, "payload_hash": payload_hash,
        }
        with self.connect() as c:
            c.execute("INSERT INTO evidence_retrieval_attempts VALUES(?,?,?,?,?,?,?,?,?,?)", (
                attempt_id, market_id, query, source_url, retrieved_at, published_at, status,
                rejection_reason, payload_hash, stable_hash(identity)))
        return attempt_id

    def insert_llm_attempt(self, values: dict[str, Any]) -> str:
        """Persist every terminal provider attempt, including malformed/API failures."""
        attempt_id = str(uuid.uuid4())
        raw = values.get("raw_response")
        raw_text = raw if isinstance(raw, str) or raw is None else canonical_json(raw)
        raw_hash = stable_hash(raw_text) if raw_text is not None else None
        identity = {key: values.get(key) for key in ("market_id", "market_snapshot_id", "eligibility_decision_id", "attempted_at", "status", "failure_code", "provider_identity", "generation_config", "prompt_version", "schema_version", "evidence_root_hash", "request_hash")}
        identity["raw_response_hash"] = raw_hash
        with self.connect() as c:
            c.execute("INSERT INTO llm_forecast_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (attempt_id, values["market_id"], values["market_snapshot_id"], values["eligibility_decision_id"], values["attempted_at"], values["status"], values.get("failure_code"), canonical_json(values["provider_identity"]), canonical_json(values["generation_config"]), values["prompt_version"], values["schema_version"], values["evidence_root_hash"], values["request_hash"], raw_text, raw_hash, stable_hash(identity)))
        return attempt_id

    def independent_forecast_context(self, market_id: str, snapshot_id: str, decision_id: str) -> dict[str, Any]:
        """Only frozen non-price context permitted in an independent LLM request."""
        with self.connect() as c:
            row = c.execute("SELECT s.market_id,m.question,s.resolution_rule_text,s.end_date,d.eligible,d.market_snapshot_id,d.market_id AS decision_market_id FROM market_snapshots s JOIN markets m ON m.market_id=s.market_id JOIN eligibility_decisions d ON d.decision_id=? WHERE s.snapshot_id=?", (decision_id, snapshot_id)).fetchone()
        if not row or row["market_id"] != market_id or row["decision_market_id"] != market_id or row["market_snapshot_id"] != snapshot_id or row["eligible"] != 1:
            raise ValueError("independent forecast requires exact eligible snapshot")
        return {"question": row["question"], "resolution_rule_text": row["resolution_rule_text"], "end_date": row["end_date"]}

    def market_probability_reveal(self, forecast_id: str) -> dict[str, Any]:
        """Read historic baseline only after a forecast has committed."""
        with self.connect() as c:
            row = c.execute("SELECT f.forecast_id,f.probability,s.yes_midpoint,s.snapshot_hash FROM forecasts f JOIN market_snapshots s ON s.snapshot_id=f.market_snapshot_id WHERE f.forecast_id=?", (forecast_id,)).fetchone()
        if not row:
            raise ValueError("forecast does not exist")
        midpoint = float(row["yes_midpoint"]) if row["yes_midpoint"] is not None else None
        return {"forecast_id": row["forecast_id"], "ai_probability": row["probability"], "market_probability": midpoint, "market_snapshot_hash": row["snapshot_hash"], "raw_residual": float(row["probability"]) - midpoint if midpoint is not None else None}

    def evidence_rows(self, market_id: str, evidence_ids: Sequence[str]) -> list[sqlite3.Row]:
        if not evidence_ids:
            return []
        placeholders = ",".join("?" for _ in evidence_ids)
        with self.connect() as c:
            rows = c.execute(f"SELECT * FROM evidence_snapshots WHERE market_id=? AND evidence_id IN ({placeholders})", (market_id, *evidence_ids)).fetchall()
        if len(rows) != len(set(evidence_ids)):
            raise ValueError("one or more evidence IDs do not belong to this market")
        by_id = {str(row["evidence_id"]): row for row in rows}
        return [by_id[value] for value in evidence_ids]

    def insert_forecast(self, values: dict[str, Any], evidence_ids: Sequence[str]) -> str:
        forecast_id = str(uuid.uuid4())
        rows = self.evidence_rows(values["market_id"], evidence_ids)
        root = stable_hash([str(row["payload_sha256"]) for row in rows])
        payload = {**values, "evidence_root_hash": root, "evidence_ids": list(evidence_ids)}
        forecast_hash = stable_hash(payload)
        with self.connect() as c:
            decision = c.execute("SELECT * FROM eligibility_decisions WHERE decision_id=?", (values["eligibility_decision_id"],)).fetchone()
            snapshot = c.execute("SELECT market_id FROM market_snapshots WHERE snapshot_id=?", (values["market_snapshot_id"],)).fetchone()
            if not decision or not snapshot or decision["eligible"] != 1 or decision["market_snapshot_id"] != values["market_snapshot_id"] or decision["market_id"] != values["market_id"] or snapshot["market_id"] != values["market_id"]:
                raise ValueError("forecast must reference an eligible exact market snapshot")
            # Resolved status is read solely from the frozen Gamma snapshot, never inferred.
            gamma = __import__("json").loads(c.execute("SELECT gamma_payload_json FROM market_snapshots WHERE snapshot_id=?", (values["market_snapshot_id"],)).fetchone()[0])
            if gamma.get("closed") is True or gamma.get("resolved") is True:
                raise ValueError("cannot forecast a resolved/closed snapshot")
            c.execute("""INSERT INTO forecasts(forecast_id,forecast_hash,market_id,market_snapshot_id,eligibility_decision_id,forecasted_at,evidence_cutoff_at,evidence_root_hash,forecast_schema_version,producer_kind,producer_identity_json,config_hash,probability,rationale,committed_at,cohort_id,forecast_methodology_hash)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (forecast_id, forecast_hash, values["market_id"], values["market_snapshot_id"], values["eligibility_decision_id"], values["forecasted_at"], values["evidence_cutoff_at"], root, values["forecast_schema_version"], values["producer_kind"], canonical_json(values["producer_identity"]), values["config_hash"], values["probability"], values["rationale"], values["committed_at"], values.get("cohort_id"), values.get("forecast_methodology_hash")))
            for ordinal, row in enumerate(rows):
                ref_hash = stable_hash({"forecast_hash": forecast_hash, "evidence_hash": row["payload_sha256"], "ordinal": ordinal})
                c.execute("INSERT INTO forecast_evidence_refs VALUES(?,?,?,?)", (forecast_id, row["evidence_id"], ordinal, ref_hash))
        return forecast_id

    def forecast_detail(self, forecast_id: str) -> dict[str, Any] | None:
        with self.connect() as c:
            forecast = c.execute("SELECT * FROM forecasts WHERE forecast_id=?", (forecast_id,)).fetchone()
            if not forecast:
                return None
            refs = c.execute("SELECT r.ordinal,e.* FROM forecast_evidence_refs r JOIN evidence_snapshots e ON e.evidence_id=r.evidence_id WHERE r.forecast_id=? ORDER BY r.ordinal", (forecast_id,)).fetchall()
        return {"forecast": dict(forecast), "evidence": [dict(row) for row in refs]}
