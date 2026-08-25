"""Read-only projections for the Polymarket research ledger.

This module deliberately has no HTTP framework dependency.  The existing
application uses :class:`ThreadingHTTPServer`; keeping projections here makes
them equally usable by that handler, tests, and a future dedicated service.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .repository import DEFAULT_DB_PATH, PolymarketRepository


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback


class PolymarketReadModel:
    """Small, projection-only query surface; raw Gamma/CLOB blobs never leave it."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or os.getenv("POLYMARKET_DB_PATH", DEFAULT_DB_PATH))

    def _repo(self) -> PolymarketRepository:
        # Repository initialization is backward-compatible and only creates
        # schema/indexes; endpoints never collect, forecast, or resolve.
        return PolymarketRepository(self.path)

    def overview(self) -> dict[str, Any]:
        repo = self._repo()
        status, stats = repo.status(), repo.research_statistics()
        with repo.connect() as c:
            latest = c.execute("SELECT completed_at FROM collection_runs WHERE status='SUCCEEDED' ORDER BY completed_at DESC LIMIT 1").fetchone()
            versions = c.execute("SELECT forecast_schema_version,prompt_version,eligibility_policy_version,evidence_policy_version FROM cohort_runs ORDER BY completed_at DESC LIMIT 1").fetchone()
        return {
            "active_universe_count": status["active_universe_count"], "eligible_markets": status["eligible_count"],
            "llm_forecasts": stats["forecast_count"], "unresolved": stats["unresolved_count"],
            "resolved": stats["resolved_count"], "scored": stats["scored_count"],
            "model_brier": stats["mean_brier_model"], "market_brier": stats["mean_brier_market"],
            "delta_brier": stats["mean_delta_brier"], "model_logloss": stats["mean_logloss_model"],
            "market_logloss": stats["mean_logloss_market"], "delta_logloss": stats["mean_delta_logloss"],
            "paper_pnl": stats["known_fee_net_pnl"], "evidence_admission_rate": stats["evidence_admission_rate"],
            "latest_collection_time": latest["completed_at"] if latest else None,
            "methodology": dict(versions) if versions else None,
            "performance_available": bool(stats["scored_count"]),
        }

    def markets(self, *, page: int = 1, page_size: int = 50, eligible: str | None = None,
                forecasted: str | None = None, unresolved: str | None = None,
                resolved: str | None = None, event: str | None = None, search: str | None = None) -> dict[str, Any]:
        page, page_size = max(1, page), min(max(1, page_size), 100)
        clauses, params = ["1=1"], []
        def yes(value: str | None) -> bool | None:
            if value is None: return None
            if value.lower() in {"1", "true", "yes"}: return True
            if value.lower() in {"0", "false", "no"}: return False
            raise ValueError("boolean filters must be true or false")
        for value, column in ((yes(eligible), "COALESCE(d.eligible,0)"), (yes(forecasted), "f.forecast_id IS NOT NULL"),
                              (yes(resolved), "r.classification='VALID_BINARY'"), (yes(unresolved), "COALESCE(r.classification,'UNRESOLVED')!='VALID_BINARY'")):
            if value is not None:
                clauses.append(f"{column}={'1' if value else '0'}")
        if event:
            clauses.append("(s.event_slug LIKE ? OR s.event_id LIKE ?)"); params.extend([f"%{event}%", f"%{event}%"])
        if search:
            clauses.append("(m.question LIKE ? OR m.slug LIKE ?)"); params.extend([f"%{search}%", f"%{search}%"])
        where = " AND ".join(clauses)
        sql = f"""
          WITH latest_s AS (SELECT s.* FROM market_snapshots s JOIN (SELECT market_id,MAX(captured_at) captured_at FROM market_snapshots GROUP BY market_id) x ON x.market_id=s.market_id AND x.captured_at=s.captured_at),
          latest_d AS (SELECT d.* FROM eligibility_decisions d JOIN (SELECT market_id,MAX(evaluated_at) evaluated_at FROM eligibility_decisions GROUP BY market_id) x ON x.market_id=d.market_id AND x.evaluated_at=d.evaluated_at),
          latest_f AS (SELECT f.* FROM forecasts f JOIN (SELECT market_id,MAX(committed_at) committed_at FROM forecasts WHERE producer_kind='LLM' GROUP BY market_id) x ON x.market_id=f.market_id AND x.committed_at=f.committed_at),
          latest_r AS (SELECT r.* FROM resolutions r JOIN (SELECT market_id,MAX(revision) revision FROM resolutions GROUP BY market_id) x ON x.market_id=r.market_id AND x.revision=r.revision)
          SELECT m.market_id,m.question,m.slug,s.event_id,s.event_slug,s.end_date,s.neg_risk,s.yes_midpoint,s.yes_best_bid,s.yes_best_ask,s.statistical_cluster_id,
                 d.eligible,d.policy_version,f.forecast_id,f.probability,f.committed_at,r.classification,r.outcome_value
          FROM markets m LEFT JOIN latest_s s ON s.market_id=m.market_id LEFT JOIN latest_d d ON d.market_id=m.market_id
          LEFT JOIN latest_f f ON f.market_id=m.market_id LEFT JOIN latest_r r ON r.market_id=m.market_id WHERE {where}
        """
        repo = self._repo()
        with repo.connect() as c:
            total = int(c.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0])
            rows = c.execute(sql + " ORDER BY COALESCE(f.committed_at,s.captured_at,m.first_seen_at) DESC, m.market_id LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
        items = []
        for row in rows:
            d = dict(row); midpoint, bid, ask = _number(d.pop("yes_midpoint")), _number(d.pop("yes_best_bid")), _number(d.pop("yes_best_ask"))
            event_slug, event_id = d.pop("event_slug"), d.pop("event_id")
            d.update({"event": event_slug or event_id, "negRisk": bool(d.pop("neg_risk") or 0), "eligibility": bool(d.pop("eligible") or 0), "market_probability": midpoint,
                      "spread": (ask - bid) if ask is not None and bid is not None else None, "forecast_status": "COMMITTED" if d.get("forecast_id") else "NONE",
                      "resolution_status": d.pop("classification") or "UNRESOLVED", "ai_probability": d.pop("probability"), "forecast_committed_at": d.pop("committed_at")})
            items.append(d)
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    def market_detail(self, market_id: str) -> dict[str, Any] | None:
        repo = self._repo()
        with repo.connect() as c:
            row = c.execute("""SELECT m.market_id,m.question,m.slug,s.*,d.eligible,d.policy_version,d.reasons_json,f.forecast_id,f.probability,f.committed_at,f.forecast_hash,f.evidence_root_hash,f.cohort_id,f.forecast_methodology_hash,r.classification,r.outcome_value,r.resolved_at
              FROM markets m JOIN market_snapshots s ON s.snapshot_id=(SELECT snapshot_id FROM market_snapshots WHERE market_id=m.market_id ORDER BY captured_at DESC LIMIT 1)
              LEFT JOIN eligibility_decisions d ON d.decision_id=(SELECT decision_id FROM eligibility_decisions WHERE market_id=m.market_id ORDER BY evaluated_at DESC LIMIT 1)
              LEFT JOIN forecasts f ON f.forecast_id=(SELECT forecast_id FROM forecasts WHERE market_id=m.market_id AND producer_kind='LLM' ORDER BY committed_at DESC LIMIT 1)
              LEFT JOIN resolutions r ON r.resolution_id=(SELECT resolution_id FROM resolutions WHERE market_id=m.market_id ORDER BY revision DESC LIMIT 1) WHERE m.market_id=?""", (market_id,)).fetchone()
        if not row: return None
        d = dict(row)
        return {"market_id": d["market_id"], "question": d["question"], "slug": d["slug"], "event": d["event_slug"] or d["event_id"], "end_date": d["end_date"], "negRisk": bool(d["neg_risk"]), "statistical_cluster_id": d["statistical_cluster_id"],
                "resolution_rules": d["resolution_rule_text"], "market_snapshot": {"snapshot_id": d["snapshot_id"], "snapshot_hash": d["snapshot_hash"], "captured_at": d["captured_at"], "yes_midpoint": _number(d["yes_midpoint"]), "yes_best_bid": _number(d["yes_best_bid"]), "yes_best_ask": _number(d["yes_best_ask"])},
                "eligibility": {"eligible": bool(d["eligible"] or 0), "policy_version": d["policy_version"], "reasons": _json(d["reasons_json"], [])},
                "forecast": self.forecast_detail(d["forecast_id"]) if d["forecast_id"] else None,
                "resolution": {"status": d["classification"] or "UNRESOLVED", "outcome_value": d["outcome_value"], "resolved_at": d["resolved_at"]}}

    def forecasts(self, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        page, page_size = max(1, page), min(max(1, page_size), 100); repo = self._repo()
        sql = """SELECT f.forecast_id,f.market_id,m.question,f.committed_at,f.probability,s.yes_midpoint,f.forecast_schema_version,f.cohort_id,f.forecast_methodology_hash,
          (SELECT raw_response FROM llm_forecast_attempts a WHERE a.market_snapshot_id=f.market_snapshot_id AND a.status='SUCCEEDED' ORDER BY attempted_at DESC LIMIT 1) llm_output,
          (SELECT COUNT(*) FROM forecast_evidence_refs e WHERE e.forecast_id=f.forecast_id) evidence_count,r.classification,r.outcome_value,sc.brier_delta,sc.log_loss_delta,sc.executable_side,sc.executable_net_pnl
          FROM forecasts f JOIN markets m ON m.market_id=f.market_id JOIN market_snapshots s ON s.snapshot_id=f.market_snapshot_id
          LEFT JOIN resolutions r ON r.resolution_id=(SELECT resolution_id FROM resolutions WHERE market_id=f.market_id ORDER BY revision DESC LIMIT 1)
          LEFT JOIN scores sc ON sc.score_id=(SELECT score_id FROM scores WHERE forecast_id=f.forecast_id ORDER BY scored_at DESC LIMIT 1) WHERE f.producer_kind='LLM'"""
        with repo.connect() as c:
            total = int(c.execute("SELECT COUNT(*) FROM forecasts WHERE producer_kind='LLM'").fetchone()[0]); rows = c.execute(sql + " ORDER BY f.committed_at DESC LIMIT ? OFFSET ?", (page_size, (page-1)*page_size)).fetchall()
        return {"items": [self._forecast_item(dict(row)) for row in rows], "page": page, "page_size": page_size, "total": total}

    def _forecast_item(self, d: dict[str, Any]) -> dict[str, Any]:
        market = _number(d.pop("yes_midpoint")); probability = _number(d.pop("probability")); d.update({"ai_probability": probability, "market_probability": market, "residual": probability - market if market is not None and probability is not None else None,
            "confidence": _json(d.pop("llm_output"), {}).get("confidence"), "resolution_status": d.pop("classification") or "UNRESOLVED", "score": {"outcome_value": d.pop("outcome_value"), "brier_delta": d.pop("brier_delta"), "log_loss_delta": d.pop("log_loss_delta"), "paper_side": d.pop("executable_side"), "paper_pnl": d.pop("executable_net_pnl")}})
        return d

    def forecast_detail(self, forecast_id: str) -> dict[str, Any] | None:
        repo = self._repo()
        with repo.connect() as c:
            row = c.execute("""SELECT f.*,m.question,s.snapshot_hash,s.yes_midpoint,s.yes_best_bid,s.yes_best_ask,s.end_date,s.resolution_rule_text,r.classification,r.outcome_value,r.resolved_at,sc.*,
              (SELECT raw_response FROM llm_forecast_attempts a WHERE a.market_snapshot_id=f.market_snapshot_id AND a.status='SUCCEEDED' ORDER BY attempted_at DESC LIMIT 1) llm_output
              FROM forecasts f JOIN markets m ON m.market_id=f.market_id JOIN market_snapshots s ON s.snapshot_id=f.market_snapshot_id
              LEFT JOIN resolutions r ON r.resolution_id=(SELECT resolution_id FROM resolutions WHERE market_id=f.market_id ORDER BY revision DESC LIMIT 1)
              LEFT JOIN scores sc ON sc.score_id=(SELECT score_id FROM scores WHERE forecast_id=f.forecast_id ORDER BY scored_at DESC LIMIT 1) WHERE f.forecast_id=?""", (forecast_id,)).fetchone()
            evidence = c.execute("SELECT e.evidence_id,e.source_url,e.source_published_at,e.evidence_cutoff_at,e.payload_sha256,e.lineage_json,r.ordinal FROM forecast_evidence_refs r JOIN evidence_snapshots e ON e.evidence_id=r.evidence_id WHERE r.forecast_id=? ORDER BY r.ordinal", (forecast_id,)).fetchall()
            cohort = c.execute("SELECT eligibility_policy_version,evidence_policy_version,forecast_schema_version,prompt_version,provider_policy_version,scoring_version,execution_simulation_version,model_identity_json FROM cohort_runs WHERE cohort_id=?", (row["cohort_id"],)).fetchone() if row and row["cohort_id"] else None
        if not row: return None
        d = dict(row); probability, midpoint = _number(d["probability"]), _number(d["yes_midpoint"]); output = _json(d["llm_output"], {})
        return {"forecast_id": d["forecast_id"], "market_id": d["market_id"], "question": d["question"], "committed_at": d["committed_at"], "forecast": {"ai_probability": probability, "market_probability": midpoint, "residual": probability-midpoint if midpoint is not None else None, "rationale": d["rationale"]},
            "frozen_market_snapshot": {"snapshot_id": d["market_snapshot_id"], "snapshot_hash": d["snapshot_hash"], "end_date": d["end_date"], "resolution_rules": d["resolution_rule_text"], "yes_best_bid": _number(d["yes_best_bid"]), "yes_best_ask": _number(d["yes_best_ask"])}, "summary": output.get("summary", d["rationale"]), "confidence": output.get("confidence"), "uncertainties": output.get("uncertainties", []),
            "evidence": [{**dict(x), "lineage": _json(x["lineage_json"], {})} for x in evidence], "audit": {"forecast_hash": d["forecast_hash"], "evidence_root_hash": d["evidence_root_hash"], "forecast_schema_version": d["forecast_schema_version"], "provider": _json(d["producer_identity_json"], {}), "methodology_hash": d["forecast_methodology_hash"]},
            "methodology": {**(dict(cohort) if cohort else {}), "model_identity": _json(cohort["model_identity_json"], {}) if cohort else {}}, "resolution": {"status": d["classification"] or "UNRESOLVED", "outcome_value": d["outcome_value"], "resolved_at": d["resolved_at"]},
            "score": {"brier_delta": d.get("brier_delta"), "log_loss_delta": d.get("log_loss_delta"), "paper_side": d.get("executable_side"), "paper_pnl": d.get("executable_net_pnl")}}

    def scoreboard(self) -> dict[str, Any]:
        stats = self._repo().research_statistics(); warning = "INSUFFICIENT_SAMPLE" if stats["scored_count"] < 30 else None
        return {"performance": {key: stats[key] for key in ("scored_count","raw_resolved_forecasts","unique_statistical_clusters","mean_brier_model","mean_brier_market","mean_delta_brier","mean_logloss_model","mean_logloss_market","mean_delta_logloss","wins_vs_market","losses_vs_market","ties")},
                "calibration": stats["calibration_bins"], "paper": {key: stats[key] for key in ("trade_count","no_trade_count","gross_pnl","known_fee_net_pnl","unknown_fee_count")}, "cohorts": self.cohorts()["items"], "sample_warning": warning, "unresolved_forecasts": stats["unresolved_count"]}

    def cohorts(self) -> dict[str, Any]:
        repo = self._repo()
        with repo.connect() as c:
            rows = c.execute("""SELECT c.cohort_id,c.completed_at,c.status,c.eligibility_policy_version,c.evidence_policy_version,c.forecast_schema_version,c.prompt_version,c.provider_policy_version,c.scoring_version,c.execution_simulation_version,
             (SELECT COUNT(*) FROM forecasts f WHERE f.cohort_id=c.cohort_id AND f.producer_kind='LLM') forecast_count,
             (SELECT COUNT(*) FROM scores s JOIN forecasts f ON f.forecast_id=s.forecast_id WHERE f.cohort_id=c.cohort_id) scored_count FROM cohort_runs c ORDER BY c.completed_at DESC""").fetchall()
        return {"items": [dict(row) for row in rows]}

    def health(self) -> dict[str, Any]:
        # Reuse the operations projection so API and CLI expose the same
        # lease, WAL-safe backup, provider-attempt, and integrity semantics.
        from .operations import health
        return health(self._repo())
