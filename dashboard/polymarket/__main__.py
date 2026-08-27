"""Local-only CLI for Phase 1A."""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from typing import Any

from .client import GAMMA_MAX_PAGE_SIZE, GAMMA_PAGINATION_POLICY_VERSION, PolymarketClient
from .eligibility import POLICY_V2_VERSION, evaluate, evaluate_v2, event_lineage, metadata_prefilter, policy_v2_hash
from .forecast import commit_manual_forecast
from .models import utc_now
from .repository import DEFAULT_DB_PATH, PolymarketRepository
from .models import stable_hash
from .resolution import resolve_market
from .llm_provider import configured_provider
from .evidence import deterministic_queries, public_search_candidates, retrieve_candidates
from .evidence import EVIDENCE_POLICY_VERSION, query_policy_hash
from .llm_provider import DEEPSEEK_PROVIDER_POLICY_VERSION, provider_policy_hash
from .llm_forecast import LLM_FORECAST_SCHEMA_VERSION, PROMPT_VERSION
from .cadence import forecast_methodology_hash, has_initial_forecast
from .collection import ForecastCandidate, collect_forecast_batch
from .scoring import DEFAULT_MINIMUM_EDGE, EXECUTION_POLICY_VERSION, SCORING_VERSION

UNIVERSE_SELECTION_POLICY_VERSION = "polymarket-universe-selection-v1"
UNIVERSE_SELECTION_POLICY = {"ordering": "market_id_ascending", "source": "gamma_active_open", "page_size": 500}
COLLECTION_POLICY_VERSION = "polymarket-local-collection-v1"
COLLECTION_POLICY = {
    "recommended_interval_hours": 3,
    "missed_schedule_backfill": False,
    "overlapping_runs": "unsupported",
}
COLLECTION_POLICY_HASH = stable_hash(
    {"version": COLLECTION_POLICY_VERSION, **COLLECTION_POLICY}
)
DEFAULT_CLOB_WORKERS = 8
METADATA_FINGERPRINT_VERSION = "polymarket-market-metadata-fingerprint-v1"


def _market_metadata_fingerprint(market: dict[str, Any]) -> str:
    """Hash only fields whose change can affect admission or audit meaning.

    Gamma adds mutable presentation/market-data fields (timestamps, liquidity,
    best quotes and nested event refreshes) to otherwise unchanged markets.
    Those are intentionally not an incremental-change signal; CLOB remains the
    frozen source for forecast-time prices.
    """
    keys = (
        "id", "slug", "question", "active", "closed", "resolved", "conditionId", "condition_id",
        "outcomes", "clobTokenIds", "endDate", "endDateIso", "end_date", "resolutionCriteria",
        "resolutionRule", "rules", "description", "marketType", "market_type", "requiresSiblingMarkets",
        "requires_sibling_markets", "resolutionRequiresEvent", "resolution_requires_event", "standaloneResolution",
        "standalone_resolution", "tags", "live", "inPlay", "in_play", "gameStartTime", "eventStartTime",
        "enableOrderBook", "acceptingOrders", "negRisk", "eventId", "event_id", "eventSlug", "event_slug",
    )
    return stable_hash({"version": METADATA_FINGERPRINT_VERSION, "fields": {key: market.get(key) for key in keys if key in market}})


def _rule(market: dict[str, Any]) -> str:
    for key in ("resolutionCriteria", "resolutionRule", "rules", "description"):
        value = market.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _end_date(market: dict[str, Any]) -> str | None:
    for key in ("endDate", "endDateIso", "end_date"):
        value = market.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _metadata_audit_projection(market: dict[str, Any], full_payload_hash: str) -> dict[str, Any]:
    """Compact every field consumed by metadata eligibility, plus raw hash."""
    keys = ("id", "slug", "question", "active", "closed", "resolved", "conditionId", "condition_id",
            "outcomes", "clobTokenIds", "endDate", "endDateIso", "end_date", "resolutionCriteria",
            "resolutionRule", "rules", "description", "marketType", "market_type", "requiresSiblingMarkets",
            "requires_sibling_markets", "resolutionRequiresEvent", "resolution_requires_event",
            "standaloneResolution", "standalone_resolution", "tags", "live", "inPlay", "in_play",
            "gameStartTime", "eventStartTime", "liquidityNum", "liquidity", "enableOrderBook",
            "acceptingOrders", "bestBid", "bestAsk", "spread", "negRisk", "eventId", "event_id",
            "eventSlug", "event_slug", "updatedAt", "updated_at")
    projection = {key: market.get(key) for key in keys if key in market}
    projection["full_gamma_payload_hash"] = full_payload_hash
    projection["storage"] = "metadata-audit-projection-v1"
    return projection


def freeze_market(client: PolymarketClient, market: dict[str, Any], *, fetch_clob: bool = True) -> dict[str, Any]:
    """Freeze Gamma first; CLOB is fetched only after metadata prefilter passes."""
    clob_requests_attempted = 0
    try:
        mapping = client.token_mapping(market)
        if fetch_clob:
            clob_requests_attempted += 1
            yes_book = client.fetch_orderbook(mapping["YES"])
            clob_requests_attempted += 1
            no_book = client.fetch_orderbook(mapping["NO"])
            quotes = {"YES": client.quote(yes_book), "NO": client.quote(no_book)}
        else:
            yes_book, no_book, quotes = {}, {}, {"YES": {}, "NO": {}}
        outcomes = ["YES", "NO"]
    except Exception:
        mapping, yes_book, no_book = {}, {}, {}
        quotes, outcomes = {"YES": {}, "NO": {}}, []
    return {"market": market, "gamma_payload": market, "captured_at": utc_now(), "source_timestamp": market.get("updatedAt") or market.get("updated_at"), "resolution_rule_text": _rule(market), "end_date": _end_date(market), "outcomes": outcomes, "token_mapping": mapping, "yes_orderbook": yes_book, "no_orderbook": no_book, "quotes": quotes, "event_lineage": event_lineage(market), "clob_requests_attempted": clob_requests_attempted}


def sync_universe(repo: PolymarketRepository, client: PolymarketClient, limit: int | None, page_size: int) -> tuple[str, list[dict[str, Any]]]:
    universe = client.fetch_active_markets(limit, page_size=page_size)
    universe_id = repo.persist_universe(universe, UNIVERSE_SELECTION_POLICY_VERSION,
        stable_hash({"version": UNIVERSE_SELECTION_POLICY_VERSION, "config": UNIVERSE_SELECTION_POLICY}))
    results: list[dict[str, Any]] = []
    for market in universe:
        snapshot = freeze_market(client, market)
        decision = evaluate(snapshot)
        snapshot_id, decision_id = repo.persist_snapshot(snapshot, decision)
        results.append({"market_id": str(market["id"]), "snapshot_id": snapshot_id, "decision_id": decision_id, **decision})
    return universe_id, results


def _active_market_pages(client: PolymarketClient, limit: int | None, page_size: int, captured: str):
    """Use the streaming client; retain a legacy seam only for small test fakes."""
    iterator = getattr(client, "iter_active_market_pages", None)
    if iterator:
        yield from iterator(limit, page_size=page_size, as_of=captured)
        return
    # Old test-only doubles predate the iterator.  Production PolymarketClient
    # always takes the branch above.
    markets = client.fetch_active_markets(limit, page_size=page_size, as_of=captured)
    for offset in range(0, len(markets), page_size):
        yield sorted(markets[offset:offset + page_size], key=lambda item: str(item["id"]))


def _freeze_page_bounded(client: PolymarketClient, markets: list[dict[str, Any]], workers: int):
    """At most ``workers`` CLOB responses are retained in addition to one page."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = []
        iterator = iter(markets)
        for _ in range(workers):
            try:
                market = next(iterator)
                pending.append((market, pool.submit(freeze_market, client, market, fetch_clob=True)))
            except StopIteration:
                break
        while pending:
            market, future = pending.pop(0)
            try:
                next_market = next(iterator)
                pending.append((next_market, pool.submit(freeze_market, client, next_market, fetch_clob=True)))
            except StopIteration:
                pass
            yield market, future.result()


def sync_universe_v2(repo: PolymarketRepository, client: PolymarketClient, limit: int | None, page_size: int, *, clob_workers: int = DEFAULT_CLOB_WORKERS, mode: str = "FULL_BOOTSTRAP", collection_run_id: str | None = None) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Stream Gamma pages through staged audit persistence and bounded CLOB IO."""
    captured = utc_now()
    policy = {**UNIVERSE_SELECTION_POLICY, "eligibility": "polymarket-eligibility-v2", "two_stage": "gamma_metadata_then_clob_candidates"}
    pagination_metadata = {"version": GAMMA_PAGINATION_POLICY_VERSION, "page_size": min(page_size, GAMMA_MAX_PAGE_SIZE),
                           "ordering": "id_ascending", "limit": limit, "prospective_as_of": captured,
                           "closed": False, "end_date_min": captured, "complete_active_universe": limit is None}
    stage_id = repo.begin_universe("polymarket-universe-selection-v2", stable_hash({"version": "polymarket-universe-selection-v2", "config": policy}), captured, pagination_metadata=pagination_metadata)
    workers = max(1, min(int(clob_workers), 32))
    totals = {"full_universe_count": 0, "metadata_candidate_count": 0, "clob_candidate_markets": 0,
              "clob_request_count": 0, "eligible_count": 0, "unchanged_metadata_observations": 0,
              "new_or_changed_metadata": 0, "max_page_markets": 0}
    try:
        for page in _active_market_pages(client, limit, min(page_size, GAMMA_MAX_PAGE_SIZE), captured):
            totals["max_page_markets"] = max(totals["max_page_markets"], len(page))
            payload_hashes = [stable_hash(market) for market in page]
            repo.append_universe_page(stage_id, page, payload_hashes)
            ids = [str(market["id"]) for market in page]
            prior = repo.latest_metadata_states(ids) if mode == "INCREMENTAL" else {}
            pending: list[tuple[dict[str, Any], list[str], str, dict[str, Any] | None]] = []
            for market, payload_hash in zip(page, payload_hashes):
                reasons = metadata_prefilter(market, captured_at=captured)
                market_id, previous = str(market["id"]), prior.get(str(market["id"]))
                fingerprint = _market_metadata_fingerprint(market)
                totals["full_universe_count"] += 1
                totals["metadata_candidate_count"] += int(not reasons)
                if mode == "INCREMENTAL" and previous and _market_metadata_fingerprint(previous["gamma_payload"]) == fingerprint:
                    totals["unchanged_metadata_observations"] += 1
                    totals["eligible_count"] += int(bool(previous["eligible"]))
                    continue
                pending.append((market, reasons, payload_hash, previous))
            clob_markets = [item[0] for item in pending if not item[1]]
            totals["clob_candidate_markets"] += len(clob_markets)
            writes: list[tuple[dict[str, Any], dict[str, Any]]] = []
            def record(market: dict[str, Any], reasons: list[str], payload_hash: str, snapshot: dict[str, Any]) -> None:
                totals["clob_request_count"] += int(snapshot.get("clob_requests_attempted", 0))
                snapshot["captured_at"] = captured; snapshot["gamma_payload_hash_override"] = payload_hash
                decision = evaluate_v2(snapshot)
                decision["reasons"] = sorted(set([*reasons, *decision["reasons"]])); decision["eligible"] = not decision["reasons"]
                snapshot["gamma_payload"] = _metadata_audit_projection(market, payload_hash)
                totals["eligible_count"] += int(bool(decision["eligible"])); totals["new_or_changed_metadata"] += 1
                writes.append((snapshot, decision))
            candidate_details = {str(market["id"]): (reasons, payload_hash) for market, reasons, payload_hash, _ in pending if not reasons}
            for market, reasons, payload_hash, _previous in pending:
                if reasons:
                    record(market, reasons, payload_hash, freeze_market(client, market, fetch_clob=False))
            for market, snapshot in _freeze_page_bounded(client, clob_markets, workers):
                reasons, payload_hash = candidate_details[str(market["id"])]
                record(market, reasons, payload_hash, snapshot)
            repo.persist_snapshots(writes)
        universe_id = repo.finalize_universe(stage_id)
    except Exception as exc:
        repo.fail_universe(stage_id, type(exc).__name__)
        raise
    totals.update({"metadata_rejected_count": totals["full_universe_count"] - totals["metadata_candidate_count"],
                   "clob_request_reduction_pct": (100.0 * (2 * totals["full_universe_count"] - totals["clob_request_count"]) / (2 * totals["full_universe_count"])) if totals["full_universe_count"] else 0.0,
                   "clob_workers": workers, "mode": mode, "unchanged_observation_rows_written": 0,
                   "pagination_policy_version": GAMMA_PAGINATION_POLICY_VERSION})
    # Kept as an empty compatibility slot while callers migrate.  It is never
    # populated with the universe and therefore cannot reintroduce O(N) RAM.
    return universe_id, [], totals


def _cohort_identity(provider: dict[str, Any]) -> dict[str, Any]:
    return {"eligibility_policy_version": POLICY_V2_VERSION, "eligibility_policy_hash": policy_v2_hash(),
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "evidence_policy_hash": stable_hash({"version": EVIDENCE_POLICY_VERSION, "query_policy_hash": query_policy_hash(), "strict_minimum": 1, "maximum_input": 3}),
        "forecast_schema_version": LLM_FORECAST_SCHEMA_VERSION, "prompt_version": PROMPT_VERSION,
        "prompt_hash": stable_hash({"version": PROMPT_VERSION, "schema": LLM_FORECAST_SCHEMA_VERSION, "price_blind": True}),
        "provider_policy_version": DEEPSEEK_PROVIDER_POLICY_VERSION, "provider_policy_hash": provider_policy_hash(),
        "model_identity": provider, "scoring_version": SCORING_VERSION,
        "execution_simulation_version": EXECUTION_POLICY_VERSION}


def run_forecast_cohort(repo: PolymarketRepository, client: PolymarketClient, *, max_forecasts: int,
                        dry_run: bool = False, page_size: int = 500,
                        clob_workers: int = DEFAULT_CLOB_WORKERS, mode: str = "FULL_BOOTSTRAP", collection_run_id: str | None = None) -> dict[str, Any]:
    """One full, prospective, deterministic universe-to-commit cohort."""
    started_at, cohort_id = utc_now(), str(uuid.uuid4())
    universe_id, _considered, pipeline = sync_universe_v2(repo, client, None, page_size, clob_workers=clob_workers, mode=mode, collection_run_id=collection_run_id)
    llm = configured_provider()
    provider_identity = llm.identity()
    identity = _cohort_identity(provider_identity)
    methodology_hash = forecast_methodology_hash(provider_identity=provider_identity,
        forecast_schema_version=LLM_FORECAST_SCHEMA_VERSION, prompt_version=PROMPT_VERSION,
        provider_policy_version=DEEPSEEK_PROVIDER_POLICY_VERSION, provider_policy_hash=provider_policy_hash())
    # The source of forecast selection is now the persisted deterministic
    # ledger, never the full in-memory universe.  The scan is explicitly
    # bounded; evidence failures consume no hidden unbounded candidate list.
    candidate_scan_limit = max(100, max_forecasts * 20)
    existing: list[ForecastCandidate] = []
    candidates: list[ForecastCandidate] = []
    for row in repo.iter_latest_eligible_candidates(scan_limit=candidate_scan_limit):
        item = ForecastCandidate(row["market_id"], row["snapshot_id"], row["decision_id"])
        if has_initial_forecast(repo, item.market_id, methodology_hash,
                                prompt_version=PROMPT_VERSION, provider_identity=provider_identity):
            existing.append(item)
        else:
            candidates.append(item)
    eligible_without_existing_count = len(candidates)
    # Re-fetch only the bounded formal cohort so every forecast binds a full raw
    # Gamma/CLOB payload observed immediately before evidence/LLM work.
    prepared: list[ForecastCandidate] = []
    refresh_rejected: list[str] = []
    forecast_refresh_clob_requests = 0
    for item in candidates:
        if len(prepared) >= max_forecasts:
            break
        try:
            market = client.fetch_market(item.market_id)
            refreshed = freeze_market(client, market, fetch_clob=True)
            forecast_refresh_clob_requests += int(refreshed.get("clob_requests_attempted", 0))
            decision = evaluate_v2(refreshed)
            snapshot_id, decision_id = repo.persist_snapshot(refreshed, decision)
        except Exception:
            refresh_rejected.append(item.market_id)
            continue
        if not decision["eligible"]:
            refresh_rejected.append(item.market_id)
            continue
        prepared.append(ForecastCandidate(item.market_id, snapshot_id, decision_id))
    candidates = prepared
    pipeline["forecast_refresh_clob_request_count"] = forecast_refresh_clob_requests
    pipeline["total_clob_request_count"] = int(pipeline["clob_request_count"]) + forecast_refresh_clob_requests
    readiness = llm.preflight()
    cutoffs: dict[str, str] = {}

    def retrieve(item: ForecastCandidate) -> list[str]:
        context = repo.independent_forecast_context(item.market_id, item.market_snapshot_id, item.eligibility_decision_id)
        cutoff = utc_now(); cutoffs[item.market_id] = cutoff
        queries = deterministic_queries(context["question"], context["resolution_rule_text"], context["end_date"])
        return retrieve_candidates(repo, market_id=item.market_id, queries=queries, evidence_cutoff_at=cutoff,
            candidates=public_search_candidates(queries), max_evidence=3)

    def commit(item: ForecastCandidate, evidence_ids: list[str]) -> dict[str, Any]:
        from .llm_forecast import run_independent_forecast
        result = run_independent_forecast(repo, market_id=item.market_id,
            market_snapshot_id=item.market_snapshot_id, eligibility_decision_id=item.eligibility_decision_id,
            evidence_ids=evidence_ids, evidence_cutoff_at=cutoffs[item.market_id],
            provider_identity=provider_identity,
            generation_config={"temperature": 0.1, "max_tokens": 300, "stream": False, "thinking": {"type": "disabled"}},
            model_call=llm.generate_structured_forecast, min_strict_evidence=1, cohort_id=cohort_id)
        return {"market_id": item.market_id, **result}

    if dry_run:
        batch = {"selected": [item.market_id for item in candidates[:max_forecasts]], "attempted": 0,
                 "successful": [], "failed": [], "skipped_existing_initial": [item.market_id for item in existing],
                 "skipped_insufficient_evidence": [], "skipped_provider_not_ready": []}
        cohort_status = "DRY_RUN"
    else:
        batch = collect_forecast_batch(candidates, max_forecasts=max_forecasts,
            provider_ready=readiness["status"] == "READY", already_forecast=lambda _: False,
            retrieve_evidence=retrieve, commit_forecast=commit)
        batch["skipped_existing_initial"] = [item.market_id for item in existing]
        cohort_status = "SUCCEEDED"
    batch["refresh_rejected"] = refresh_rejected
    selected_by_id = {item.market_id: item for item in candidates[:max_forecasts]}
    successful_by_id = {str(row["market_id"]): row for row in batch["successful"]}
    failed_by_id = {str(row["market_id"]): row for row in batch["failed"]}
    market_results: list[dict[str, Any]] = []
    for market_id in batch["selected"]:
        item = selected_by_id[market_id]
        if market_id in successful_by_id:
            row = successful_by_id[market_id]
            status, forecast_id, detail = "FORECAST_COMMITTED", row["forecast_id"], {"probability": row["output"]["probability_yes"], "market_reveal": row["market_reveal"]}
        elif market_id in failed_by_id:
            status, forecast_id, detail = "FORECAST_FAILED", None, failed_by_id[market_id]
        elif market_id in batch["skipped_insufficient_evidence"]:
            status, forecast_id, detail = "SKIP_INSUFFICIENT_EVIDENCE", None, {}
        elif market_id in batch["skipped_provider_not_ready"]:
            status, forecast_id, detail = "SKIPPED_PROVIDER_NOT_READY", None, {}
        else:
            status, forecast_id, detail = "DRY_RUN_SELECTED", None, {}
        market_results.append({"market_id": market_id, "market_snapshot_id": item.market_snapshot_id,
            "eligibility_decision_id": item.eligibility_decision_id, "status": status,
            "forecast_id": forecast_id, "detail": detail})
    completed_at = utc_now()
    config = {"collection_policy_version": COLLECTION_POLICY_VERSION,
              "collection_policy_hash": COLLECTION_POLICY_HASH,
              "recommended_interval_hours": COLLECTION_POLICY["recommended_interval_hours"],
              "max_forecasts": max_forecasts,
              "selection_ordering": "market_id_ascending", "dry_run": dry_run,
              "forecast_methodology_hash": methodology_hash, "clob_workers": clob_workers,
              "missed_schedule_backfill": COLLECTION_POLICY["missed_schedule_backfill"]}
    repo.insert_cohort({"cohort_id": cohort_id, "universe_snapshot_id": universe_id,
        "started_at": started_at, "completed_at": completed_at, "status": cohort_status,
        **identity, "config": config}, market_results)
    return {"cohort_id": cohort_id, "universe_snapshot_id": universe_id, "started_at": started_at,
        "completed_at": completed_at, "dry_run": dry_run, "pipeline": pipeline,
        "eligible_count": int(pipeline["eligible_count"]), "eligible_without_existing_initial": eligible_without_existing_count,
        "provider_readiness": readiness, **batch}


def resolve_forecast_markets(repo: PolymarketRepository, client: PolymarketClient) -> dict[str, Any]:
    results, failures = [], []
    for market_id in repo.forecast_market_ids():
        try:
            results.append(resolve_market(repo, client, market_id))
        except Exception as exc:
            failures.append({"market_id": market_id, "failure_code": type(exc).__name__})
    counts: dict[str, int] = {}
    for row in results:
        key = str(row["classification"]); counts[key] = counts.get(key, 0) + 1
    return {"checked": len(results), "classifications": counts, "failures": failures}


def run_collection(repo: PolymarketRepository, *, max_forecasts: int, dry_run: bool,
                   page_size: int, clob_workers: int, incremental: bool = False) -> dict[str, Any]:
    started_at = utc_now(); client = PolymarketClient(); db_before = repo.path.stat().st_size if repo.path.exists() else 0
    provisional_run_id = str(uuid.uuid4())
    from .operations import disk_guard
    guard = disk_guard(repo)
    if not guard['safe']:
        summary = {'mode': 'INCREMENTAL' if incremental else 'FULL_BOOTSTRAP', 'disk_guard': guard,
                   'db_growth_bytes': 0, 'skip_reason': 'LOW_DISK_SPACE'}
        repo.insert_collection_run({'collection_run_id': provisional_run_id, 'cohort_id': None, 'started_at': started_at,
            'completed_at': utc_now(), 'status': 'SKIPPED_LOW_DISK_SPACE', 'summary': summary,
            'error_code': 'LOW_DISK_SPACE'})
        return {'collection_run_id': provisional_run_id, 'status': 'SKIPPED_LOW_DISK_SPACE', **summary}
    if not repo.acquire_collection_lease(collection_run_id=provisional_run_id, host_identity=socket.gethostname(), process_identity=str(os.getpid())):
        raise RuntimeError('COLLECTION_LOCKED')
    mode = 'INCREMENTAL' if incremental else 'FULL_BOOTSTRAP'
    # Gamma discovery can take longer than the stale-lease window on a slow
    # connection.  Renew independently from collection work so another
    # scheduler cannot mistake a healthy process for a crashed one.
    heartbeat_stop = Event()
    def renew_lease() -> None:
        while not heartbeat_stop.wait(60):
            try:
                if not repo.heartbeat_collection_lease(provisional_run_id):
                    return
            except Exception:
                # The owner must still finish or fail deterministically; a
                # transient heartbeat write must not release the lease.
                continue
    heartbeat_thread = Thread(target=renew_lease, name='polymarket-collection-lease', daemon=True)
    heartbeat_thread.start()
    try:
        cohort = run_forecast_cohort(repo, client, max_forecasts=max_forecasts, dry_run=dry_run,
                                     page_size=page_size, clob_workers=clob_workers, mode=mode, collection_run_id=provisional_run_id)
        resolution = {"checked": 0, "classifications": {}, "failures": []} if dry_run else resolve_forecast_markets(repo, client)
        scored = 0 if dry_run else repo.score_resolved_forecasts(contracts=1.0, minimum_edge=DEFAULT_MINIMUM_EDGE, fee_model_version="UNKNOWN")
        summary = {**cohort, "mode": mode, "resolution": resolution, "new_scores": scored,
                   "db_growth_bytes": (repo.path.stat().st_size if repo.path.exists() else 0) - db_before}
    except Exception as exc:
        repo.insert_collection_run({"collection_run_id": provisional_run_id, "cohort_id": None, "started_at": started_at, "completed_at": utc_now(),
            "status": "FAILED", "summary": {"mode": mode, "interrupted": bool(getattr(exc, 'interruption_code', None))},
            "error_code": str(getattr(exc, 'interruption_code', type(exc).__name__))})
        raise
    else:
        run_id = repo.insert_collection_run({"collection_run_id": provisional_run_id, "cohort_id": cohort["cohort_id"], "started_at": started_at,
            "completed_at": utc_now(), "status": "DRY_RUN" if dry_run else "SUCCEEDED", "summary": summary, "error_code": None})
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)
        repo.release_collection_lease(provisional_run_id)
    return {"collection_run_id": run_id, **summary, "status": repo.operational_status()}


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m dashboard.polymarket")
    parser.add_argument("--db", type=Path, default=Path(os.getenv('POLYMARKET_DB_PATH', str(DEFAULT_DB_PATH))))
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync"); sync.add_argument("--limit", type=int, default=None); sync.add_argument("--page-size", type=int, default=500)
    forecast = sub.add_parser("forecast"); forecast.add_argument("--market-id", required=True); forecast.add_argument("--probability", required=True, type=float); forecast.add_argument("--producer", default="MANUAL"); forecast.add_argument("--rationale", default="manual forecast"); forecast.add_argument("--evidence-id", action="append", default=[])
    resolve = sub.add_parser("resolve"); resolve.add_argument("--market-id", action="append", default=[])
    score = sub.add_parser("score"); score.add_argument("--contracts", type=float, default=1.0); score.add_argument("--minimum-edge", type=float, default=DEFAULT_MINIMUM_EDGE); score.add_argument("--fee-model-version", default="UNKNOWN"); score.add_argument("--estimated-fee", type=float)
    sub.add_parser("status")
    sub.add_parser("llm-check")
    cohort = sub.add_parser("run-cohort"); cohort.add_argument("--max-forecasts", "--max-markets", dest="max_forecasts", type=int, default=5); cohort.add_argument("--page-size", type=int, default=500); cohort.add_argument("--clob-workers", type=int, default=DEFAULT_CLOB_WORKERS); cohort.add_argument("--dry-run", action="store_true")
    collect = sub.add_parser("collect"); collect.add_argument("--max-forecasts", type=int, default=5); collect.add_argument("--page-size", type=int, default=500); collect.add_argument("--clob-workers", type=int, default=DEFAULT_CLOB_WORKERS); collect.add_argument("--dry-run", action="store_true"); collect.add_argument("--incremental", action="store_true")
    backup = sub.add_parser("backup"); backup.add_argument("--directory", type=Path, default=None)
    backup_verify = sub.add_parser("backup-verify"); backup_verify.add_argument("path", type=Path)
    sub.add_parser("storage-status")
    sub.add_parser("db-maintenance")
    health = sub.add_parser("health"); health.add_argument("--backup-directory", type=Path, default=None)
    args = parser.parse_args(); repo = PolymarketRepository(args.db)
    if args.command == "sync":
        universe_id, results = sync_universe(repo, PolymarketClient(), args.limit, args.page_size)
        print(json.dumps({"universe_snapshot_id": universe_id, "considered": len(results), "markets": results}, ensure_ascii=False, indent=2)); return 0
    if args.command == "resolve":
        client = PolymarketClient()
        ids = args.market_id or repo.forecast_market_ids()
        results = [resolve_market(repo, client, market_id) for market_id in sorted(set(ids))]
        print(json.dumps(results, ensure_ascii=False, indent=2)); return 0
    if args.command == "score":
        print(json.dumps({"inserted": repo.score_resolved_forecasts(contracts=args.contracts, minimum_edge=args.minimum_edge, fee_model_version=args.fee_model_version, estimated_fee=args.estimated_fee)}, indent=2)); return 0
    if args.command == "status":
        status = repo.operational_status(); status["system"]["provider_readiness"] = configured_provider().preflight()
        print(json.dumps(status, indent=2)); return 0
    if args.command == "llm-check":
        # Provider preflight contains only configuration status, never credentials.
        print(json.dumps(configured_provider().preflight(), indent=2)); return 0
    if args.command == "storage-status":
        print(json.dumps(repo.storage_status(), ensure_ascii=False, indent=2)); return 0
    if args.command == "db-maintenance":
        from .operations import database_maintenance
        print(json.dumps(database_maintenance(repo), ensure_ascii=False, indent=2)); return 0
    if args.command == "backup":
        from .operations import online_backup
        print(json.dumps(online_backup(repo, args.directory or repo.path.parent / 'polymarket_backups'), ensure_ascii=False, indent=2)); return 0
    if args.command == "backup-verify":
        from .operations import verify_backup
        print(json.dumps(verify_backup(args.path), ensure_ascii=False, indent=2)); return 0
    if args.command == "health":
        from .operations import health
        print(json.dumps(health(repo, args.backup_directory), ensure_ascii=False, indent=2)); return 0
    if args.command == "run-cohort":
        if not 1 <= args.max_forecasts <= 10:
            raise ValueError("max-forecasts must be 1..10")
        print(json.dumps(run_forecast_cohort(repo, PolymarketClient(), max_forecasts=args.max_forecasts,
            dry_run=args.dry_run, page_size=args.page_size, clob_workers=args.clob_workers), ensure_ascii=False, indent=2)); return 0
    if args.command == "collect":
        if not 1 <= args.max_forecasts <= 10:
            raise ValueError("max-forecasts must be 1..10")
        class CollectionInterrupted(RuntimeError):
            def __init__(self, code: str) -> None:
                super().__init__(code); self.interruption_code = code
        previous_handlers: dict[int, Any] = {}
        def interrupt(signum: int, _frame: Any) -> None:
            raise CollectionInterrupted('TIMEOUT' if signum == getattr(signal, 'SIGTERM', -1) else 'INTERRUPTED')
        for signum in (signal.SIGINT, getattr(signal, 'SIGTERM', signal.SIGINT)):
            previous_handlers[signum] = signal.getsignal(signum); signal.signal(signum, interrupt)
        try:
            result = run_collection(repo, max_forecasts=args.max_forecasts, dry_run=args.dry_run,
                page_size=args.page_size, clob_workers=args.clob_workers, incremental=args.incremental)
        finally:
            for signum, handler in previous_handlers.items(): signal.signal(signum, handler)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    forecast_id = commit_manual_forecast(repo, args.market_id, args.probability, args.producer, args.rationale, args.evidence_id)
    print(json.dumps({"forecast_id": forecast_id}, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
