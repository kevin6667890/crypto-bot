/**
 * AUTO-GENERATED from openapi/openapi.json by npm run api:generate.
 * DO NOT EDIT. Update the local schema and regenerate instead.
 */

export interface paths {
    "/api/operations/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getOperationsSummary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/operations/trends": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getOperationsTrends"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/research/microstructure/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getMicrostructureHealth"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/research/microstructure/coverage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getMicrostructureCoverage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/research/microstructure/eligibility": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getMicrostructureEligibility"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/paper/flow/history/v1": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getFlowHistory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/data-coverage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: operations["getDataCoverage"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/market/context": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** @description Read-only causal facts and indicators from bounded confirmed data. It never emits a trading signal or invokes order logic. */
        get: operations["getMarketAnalysisContextV2"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/market/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** @description Read-only deterministic market-state recognition. This is not a trading signal. */
        get: operations["getMarketStateV2"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/market/state/compare": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** @description Bounded comparison of exactly two causal market contexts. */
        get: operations["compareMarketStatesV2"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/strategy/route": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** @description Read-only deterministic research strategy routing. It never creates orders or calls the legacy decision engine. */
        get: operations["getStrategyRouteV2"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/strategy/route/evaluate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** @description Development-only fixture evaluation. Disabled unless ENABLE_STRATEGY_ROUTER_FIXTURE_API is explicit. */
        post: operations["evaluateStrategyRouteFixtureV2"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * Format: int64
         * @description Unix timestamp in whole seconds.
         */
        UnixSeconds: number;
        /**
         * Format: int64
         * @description Unix timestamp in whole milliseconds.
         */
        UnixMilliseconds: number;
        /**
         * Format: date-time
         * @description RFC 3339 UTC timestamp.
         */
        IsoTimestamp: string;
        /**
         * @default 15m
         * @enum {string}
         */
        MarketTimeframeV2: "15m" | "1H" | "4H" | "1D" | "1W";
        IndicatorValueV2: {
            value: (number | string) | null;
            source_timestamp: components["schemas"]["UnixSeconds"] | null;
            available: boolean;
            stale: boolean;
            partial: boolean;
            warmup_complete: boolean;
            calculation_version: string;
        };
        DataGapV2: {
            source?: string;
            start: components["schemas"]["UnixSeconds"];
            end: components["schemas"]["UnixSeconds"];
            missing_bars: number;
        };
        DataQualityV2: {
            /** @enum {string} */
            status: "AVAILABLE" | "STALE" | "PARTIAL" | "MISSING";
            source_timestamp: components["schemas"]["UnixSeconds"] | null;
            stale: boolean;
            partial: boolean;
            missing: boolean;
            gaps: components["schemas"]["DataGapV2"][];
            notes: string[];
        };
        IndicatorGroupV2: {
            [key: string]: components["schemas"]["IndicatorValueV2"];
        };
        TimeframeMarketContextV2: {
            candle_close_ts: components["schemas"]["UnixSeconds"] | null;
            confirmed: boolean;
            trend: components["schemas"]["IndicatorGroupV2"];
            momentum: components["schemas"]["IndicatorGroupV2"];
            volatility: components["schemas"]["IndicatorGroupV2"];
            structure: components["schemas"]["IndicatorGroupV2"];
            volume: components["schemas"]["IndicatorGroupV2"];
            quality: components["schemas"]["DataQualityV2"];
        };
        MarketLevelV2: {
            type: string;
            timeframe: string;
            value: number;
            source_timestamp: components["schemas"]["UnixSeconds"];
            distance_pct: number;
            touches: number;
            confirmed: boolean;
            confluence_sources: string[];
            calculation_version: string;
        };
        CombinationFactV2: {
            /** @enum {string} */
            state: "PRICE_UP_OI_UP" | "PRICE_UP_OI_DOWN" | "PRICE_DOWN_OI_UP" | "PRICE_DOWN_OI_DOWN" | "PRICE_UP_CVD_UP" | "PRICE_UP_CVD_DOWN" | "PRICE_DOWN_CVD_UP" | "PRICE_DOWN_CVD_DOWN" | "INSUFFICIENT_DATA";
            observation_window_seconds: number;
            start_timestamp: components["schemas"]["UnixSeconds"] | null;
            end_timestamp: components["schemas"]["UnixSeconds"] | null;
            price_change_pct: number | null;
            oi_change?: number | null;
            cvd_change?: number | null;
            data_quality: string;
            calculation_version: string;
        };
        FlowContextV2: {
            cvd: components["schemas"]["IndicatorGroupV2"];
            oi: components["schemas"]["IndicatorGroupV2"];
            funding: components["schemas"]["IndicatorGroupV2"];
            basis: components["schemas"]["IndicatorGroupV2"];
            vpvr: components["schemas"]["IndicatorGroupV2"];
            price_oi_combination: components["schemas"]["CombinationFactV2"];
            price_cvd_combination: components["schemas"]["CombinationFactV2"];
        };
        OverallDataQualityV2: {
            /** @enum {string} */
            overall_status: "AVAILABLE" | "STALE" | "PARTIAL" | "MISSING";
            stale_sources: string[];
            partial_sources: string[];
            missing_sources: string[];
            gaps: components["schemas"]["DataGapV2"][];
        };
        MarketAnalysisContextV2: {
            /** @enum {string} */
            version: "market-analysis-context-v2";
            instrument: string;
            as_of: components["schemas"]["UnixSeconds"];
            execution_timeframe: components["schemas"]["MarketTimeframeV2"];
            price: components["schemas"]["IndicatorValueV2"];
            timeframes: {
                "15m": components["schemas"]["TimeframeMarketContextV2"];
                "1H": components["schemas"]["TimeframeMarketContextV2"];
                "4H": components["schemas"]["TimeframeMarketContextV2"];
                "1D": components["schemas"]["TimeframeMarketContextV2"];
                "1W": components["schemas"]["TimeframeMarketContextV2"];
            };
            flow: components["schemas"]["FlowContextV2"];
            levels: components["schemas"]["MarketLevelV2"][];
            quality: components["schemas"]["OverallDataQualityV2"];
        };
        StateEvidenceV2: {
            code: string;
            timeframe: string;
            value: unknown;
            weight: number;
            source_timestamp: components["schemas"]["UnixSeconds"] | null;
            quality: string;
            /** @enum {string} */
            classification: "supporting" | "conflicting" | "unavailable";
        };
        TimeframeStateV2: {
            timeframe: components["schemas"]["MarketTimeframeV2"];
            role: string;
            /** @enum {string} */
            primary_state: "TREND_UP" | "TREND_DOWN" | "RANGE_LOW_VOLATILITY" | "RANGE_HIGH_VOLATILITY" | "TRANSITION_UP" | "TRANSITION_DOWN" | "TRANSITION_MIXED" | "UNKNOWN";
            primary_state_code: string;
            evidence_strength: number;
            quality: components["schemas"]["DataQualityV2"];
            momentum_state: string;
            overlays: string[];
            source_timestamps: components["schemas"]["UnixSeconds"][];
            supporting_evidence: string[];
            conflicting_evidence: string[];
            unavailable_evidence: string[];
            limitations: string[];
        };
        LevelInteractionV2: {
            level_type: string;
            timeframe: string;
            zone_low: number;
            zone_high: number;
            boundary: number;
            distance_pct: number;
            approach_direction: string;
            /** @enum {string} */
            interaction_type: "APPROACHING" | "TOUCHING" | "REJECTED" | "BROKEN" | "RECLAIMED" | "RETESTING" | "INSIDE_ZONE" | "UNKNOWN";
            touch_count: number;
            rejection_strength?: number | null;
            reclaim_status: string;
            source_timestamps: components["schemas"]["UnixSeconds"][];
            quality: string;
            breakout_timestamp?: components["schemas"]["UnixSeconds"] | null;
            confirmation_timestamp?: components["schemas"]["UnixSeconds"] | null;
            reclaim_timestamp?: components["schemas"]["UnixSeconds"] | null;
            volume_ratio?: number | null;
            cvd_oi_quality: string;
            current_stage: string;
            invalidation_reason?: string | null;
        };
        CrossTimeframeAlignmentV2: {
            state: string;
            supporting_timeframes: string[];
            conflicting_timeframes: string[];
            missing_timeframes: string[];
            normal_pullback: boolean;
            countertrend_lower_timeframe_move: boolean;
            structure_state: string;
            environment_state: string;
            setup_state: string;
            trigger_state: string;
        };
        StateTransitionV2: {
            from_state: string;
            to_state: string;
            transition_timestamp: components["schemas"]["UnixSeconds"];
            trigger_evidence: string[];
            source_candle_timestamps: components["schemas"]["UnixSeconds"][];
            confirmation_status: string;
            invalidation_reason: string | null;
        };
        MarketStateSnapshotV2: {
            /** @enum {string} */
            version: "market-state-engine-v2";
            definition_version: string;
            instrument: string;
            as_of: components["schemas"]["UnixSeconds"];
            execution_timeframe: components["schemas"]["MarketTimeframeV2"];
            primary_state: string;
            primary_state_code: string;
            evidence_strength: number;
            quality: components["schemas"]["OverallDataQualityV2"];
            timeframes: {
                [key: string]: components["schemas"]["TimeframeStateV2"];
            };
            cross_timeframe: components["schemas"]["CrossTimeframeAlignmentV2"];
            level_interactions: components["schemas"]["LevelInteractionV2"][];
            overlays: string[];
            transitions: components["schemas"]["StateTransitionV2"][];
            evidence: components["schemas"]["StateEvidenceV2"][];
            limitations: string[];
        };
        MarketStateComparisonV2: {
            version: string;
            previous: components["schemas"]["MarketStateSnapshotV2"];
            current: components["schemas"]["MarketStateSnapshotV2"];
            transitions: components["schemas"]["StateTransitionV2"][];
        };
        StrategyEvidenceV2: {
            code: string;
            dimension: string;
            timeframe: string;
            /** @enum {string} */
            classification: "supporting" | "conflicting";
            strength: string;
            source_timestamp: components["schemas"]["UnixSeconds"] | null;
            detail: string;
        };
        StrategyBlockerV2: {
            code: string;
            timeframe: string;
            evidence: string[];
            source_timestamp: components["schemas"]["UnixSeconds"] | null;
            blocking: boolean;
            release_condition: string;
        };
        StrategyGeometryV2: {
            valid: boolean;
            setup_zone: {
                [key: string]: unknown;
            };
            trigger_boundary: {
                [key: string]: unknown;
            };
            confirmation_rule: string[];
            invalidation_reference: {
                [key: string]: unknown;
            };
            stop_reference_type: string;
            target_reference_types: string[];
            maximum_wait_bars: number;
            maximum_holding_bars: number;
            minimum_structural_reward_risk: number;
            structural_reward_risk: number | null;
            entry_timing: string;
            intrabar_policy_placeholder: string;
            gap_policy_placeholder: string;
            limitations: string[];
        };
        StrategyStageV2: {
            /** @enum {string} */
            state: "INELIGIBLE" | "WATCH" | "ARMED" | "TRIGGER_READY" | "TRIGGERED_RESEARCH_ONLY" | "INVALIDATED" | "EXPIRED" | "COOLDOWN_RESEARCH_ONLY";
            setup_started_at: components["schemas"]["UnixSeconds"] | null;
            trigger_timestamp: components["schemas"]["UnixSeconds"] | null;
            expires_at: components["schemas"]["UnixSeconds"] | null;
            rearm_after: components["schemas"]["UnixSeconds"] | null;
        };
        StrategyIdentityV2: {
            strategy_family_id: string;
            strategy_setup_id: string;
            strategy_evaluation_id: string;
            configuration_hash: string;
            family: string;
            /** @enum {string} */
            direction: "LONG" | "SHORT";
            strategy_version: string;
            definitions_version: string;
            parameter_set_version: string;
            instrument: string;
            execution_timeframe: string;
            setup_timeframe: string;
            environment_timeframe: string;
            context_timeframes: string[];
            source_candle_timestamps: components["schemas"]["UnixSeconds"][];
            level_identity: string;
            setup_started_at: components["schemas"]["UnixSeconds"] | null;
            trigger_timestamp: components["schemas"]["UnixSeconds"] | null;
        };
        StrategyCandidateV2: {
            family: string;
            /** @enum {string} */
            direction: "LONG" | "SHORT";
            strategy_version: string;
            parameter_set_version: string;
            state: string;
            stage: components["schemas"]["StrategyStageV2"];
            score: number;
            score_breakdown: {
                [key: string]: number;
            };
            evidence_strength: number;
            supporting_evidence: components["schemas"]["StrategyEvidenceV2"][];
            conflicting_evidence: components["schemas"]["StrategyEvidenceV2"][];
            blockers: components["schemas"]["StrategyBlockerV2"][];
            next_confirmation: string[];
            geometry: components["schemas"]["StrategyGeometryV2"];
            source_timestamps: components["schemas"]["UnixSeconds"][];
            data_quality: {
                [key: string]: unknown;
            };
            identity: components["schemas"]["StrategyIdentityV2"];
            identity_hash: string;
            limitations: string[];
            selection_status: string;
            selection_reason: string;
        };
        NoTradeReasonV2: {
            code: string;
            timeframe: string;
            evidence: string[];
            source_timestamp: components["schemas"]["UnixSeconds"] | null;
            temporary: boolean;
            release_condition: string;
        };
        StrategyTransitionV2: {
            strategy_setup_id: string;
            from_state: string;
            to_state: string;
            transition_timestamp: components["schemas"]["UnixSeconds"];
            reason: string;
            idempotency_key: string;
        };
        StrategyRouteSnapshotV2: {
            /** @enum {string} */
            version: "strategy-router-v2";
            /** @enum {string} */
            definitions_version: "strategy-family-definitions-v2.1";
            instrument: string;
            as_of: components["schemas"]["UnixSeconds"];
            market_context_version: string;
            market_state_version: string;
            execution_timeframe: string;
            timeframe_roles: {
                [key: string]: unknown;
            };
            primary_route: components["schemas"]["StrategyCandidateV2"] | null;
            alternatives: components["schemas"]["StrategyCandidateV2"][];
            candidates: components["schemas"]["StrategyCandidateV2"][];
            no_trade: {
                active: boolean;
                strategy_version: string;
                reasons: components["schemas"]["NoTradeReasonV2"][];
            };
            quality: {
                [key: string]: unknown;
            };
            transitions: components["schemas"]["StrategyTransitionV2"][];
            disclaimer: string;
        };
        OperationsSummary: {
            generated_at: components["schemas"]["IsoTimestamp"];
            service: {
                status: string;
                version: string;
                git_commit: string;
                uptime_seconds: number;
            };
            frontend: {
                status: string;
            };
            paper_api: {
                status: string;
                collector_freshness: {
                    [key: string]: {
                        updated_at?: components["schemas"]["IsoTimestamp"] | null;
                        age_seconds?: number | null;
                        status: string;
                    };
                };
            };
            collector: {
                status: string;
                queue_depth: number;
                last_success_data_time?: components["schemas"]["IsoTimestamp"] | null;
                write_latency_ms?: number | null;
            } & {
                [key: string]: unknown;
            };
            query_plane: {
                status: string;
                components: {
                    [key: string]: string;
                };
            };
            database: {
                status: string;
                quick_status: string;
                logical_size_bytes: number;
                microstructure_logical_size_bytes: number;
            };
            wal_size_bytes: number;
            maintenance: {
                status: string;
                telemetry_available: boolean;
                last_duration_ms?: number | null;
                checkpoint_duration_ms?: number | null;
                paused_reason?: string | null;
            } & {
                [key: string]: unknown;
            };
            scheduler: {
                running: boolean;
                last_cycle_completed_at?: components["schemas"]["IsoTimestamp"] | null;
                last_cycle_duration_ms?: number | null;
            };
            tasks: {
                current_count: number;
                queued_count: number;
                recent_completed: {
                    id: number;
                    job_type: string;
                    status: string;
                    completed_at?: components["schemas"]["IsoTimestamp"] | null;
                }[];
            };
            warning_count: number;
            storage: {
                root: {
                    total_bytes: number;
                    used_bytes: number;
                    free_bytes: number;
                    usage_percent: number;
                };
                paper_database_bytes: number;
                microstructure_database_bytes: number;
                snapshot_bytes_per_day?: number | null;
                raw_trades_bytes_per_day?: number | null;
                snapshot_mode: string;
                raw_retention_status: string;
                last_archive?: string | null;
                last_offhost_ack?: string | null;
                archive_backlog?: number | null;
                prune_backlog?: number | null;
                projection: {
                    status: string;
                    window?: string | null;
                    to_85_percent?: number | null;
                    to_90_percent?: number | null;
                } & {
                    [key: string]: unknown;
                };
                protection: {
                    /** @enum {string} */
                    level: "NORMAL" | "WARNING" | "CRITICAL" | "EMERGENCY";
                    core_ledger_allowed: boolean;
                    optional_artifacts_allowed: boolean;
                } & {
                    [key: string]: unknown;
                };
            };
            system: {
                disk_percent: number;
                memory_percent?: number | null;
            };
        };
        TrendPoint: {
            timestamp: components["schemas"]["UnixSeconds"];
            health_latency_ms?: number | null;
            coverage_latency_ms?: number | null;
            eligibility_latency_ms?: number | null;
            wal_size_bytes?: number | null;
            maintenance_duration_ms?: number | null;
            checkpoint_duration_ms?: number | null;
            queue_depth?: number | null;
            live_lag_seconds?: number | null;
            iowait_percent?: number | null;
            critical_gap_count?: number | null;
            service_state: string;
            anomaly: boolean;
        };
        OperationsTrends: {
            enabled: boolean;
            /** @enum {string} */
            window: "1h" | "6h" | "24h";
            points: components["schemas"]["TrendPoint"][];
            latency?: {
                p50_ms?: number | null;
                p95_ms?: number | null;
            };
        };
        MicrostructureHealth: {
            service_status: string;
            live_lag_seconds?: number | null;
            critical_gap_count?: number | null;
        } & {
            [key: string]: unknown;
        };
        CoverageRow: {
            instrument: string;
            earliest_ms?: components["schemas"]["UnixMilliseconds"] | null;
            latest_ms?: components["schemas"]["UnixMilliseconds"] | null;
            rows: number;
        } & {
            [key: string]: unknown;
        };
        CoverageSummary: {
            trades?: components["schemas"]["CoverageRow"][];
            oi?: components["schemas"]["CoverageRow"][];
            funding_settled?: components["schemas"]["CoverageRow"][];
            funding_predicted?: components["schemas"]["CoverageRow"][];
            mark?: components["schemas"]["CoverageRow"][];
            index?: components["schemas"]["CoverageRow"][];
            liquidations?: components["schemas"]["CoverageRow"][];
            _snapshot?: {
                generated_at?: string | null;
                data_as_of?: string | null;
                stale_seconds?: number | null;
                refreshing?: boolean;
            } & {
                [key: string]: unknown;
            };
        };
        EligibilityInstrument: {
            source_days: number;
            source_rows: number;
            gap_adjusted_usable_days: number;
            label_earliest_ms?: components["schemas"]["UnixMilliseconds"] | null;
            label_latest_ms?: components["schemas"]["UnixMilliseconds"] | null;
            overlap_usable_days: number;
            event_count: number;
            source_data_status: string;
            event_study_status: string;
            next_eligibility_date?: string | null;
            blocking_reason?: string | null;
        } & {
            [key: string]: unknown;
        };
        EligibilityFeatureGroup: {
            features: string[];
            instruments: {
                [key: string]: components["schemas"]["EligibilityInstrument"];
            };
            source_usable_days: number;
            gap_adjusted_usable_days: number;
            source_observation_count: number;
            overlap_usable_days: number;
            event_count: number;
            source_data_status: string;
            event_study_status: string;
            usable_days: number;
            gap_adjusted_sample_days: number;
            status: string;
            gap_reason?: string | null;
            blocking_reason?: string | null;
            next_eligibility_date?: string | null;
        } & {
            [key: string]: unknown;
        };
        EligibilitySummary: {
            _snapshot?: {
                [key: string]: unknown;
            } | null;
            aggregate_policy?: string | null;
            feature_groups: {
                [key: string]: components["schemas"]["EligibilityFeatureGroup"];
            };
        };
        FlowHistoryPoint: {
            time: components["schemas"]["UnixSeconds"];
            value?: number | null;
            delta?: number | null;
            trades?: number | null;
            min?: number | null;
            max?: number | null;
            observation_count?: number | null;
            /** @enum {string} */
            status: "VALID" | "WHITESPACE" | "PARTIAL" | "PARTIAL_AFTER_GAP" | "MISSING" | "UNRECOVERABLE_RAW_GAP" | "BACKFILLED_OFFICIAL" | "ARCHIVED_CONFIRMED" | "CONFLICT" | "SOURCE_UNAVAILABLE";
            quality_status?: string | null;
            gap_reason?: string | null;
            source_fingerprint?: string | null;
            source_complete: boolean;
            partial_after_gap: boolean;
            segment_start?: boolean;
        };
        FlowHistoryResponse: {
            api_version: string;
            schema_version: string;
            history_version: string;
            canonical_version?: string;
            canonical_generation?: string;
            instrument: string;
            /** @enum {string} */
            series: "cvd" | "oi";
            /** @enum {string} */
            timeframe: "1m" | "5m" | "15m" | "1h" | "4h" | "1D";
            requested_resolution: string;
            actual_resolution: string;
            /** @enum {string|null} */
            cvd_mode?: "CONTINUOUS" | "UTC_DAILY_RESET" | null;
            requested_start: components["schemas"]["UnixSeconds"];
            requested_end: components["schemas"]["UnixSeconds"];
            available_start: components["schemas"]["UnixSeconds"] | null;
            available_end: components["schemas"]["UnixSeconds"] | null;
            latest_timestamp: components["schemas"]["UnixSeconds"] | null;
            raw_row_count?: number;
            returned_point_count: number;
            resolution: string | null;
            resolution_seconds: number | null;
            stale: boolean;
            stale_after_seconds: number;
            status: string;
            gap_reason: string | null;
            source_coverage: {
                [key: string]: unknown;
            };
            coverage?: {
                [key: string]: unknown;
            };
            data_as_of: components["schemas"]["UnixSeconds"] | null;
            last_completed_bucket: components["schemas"]["UnixSeconds"];
            next_expected_bucket: components["schemas"]["UnixSeconds"];
            has_history: boolean;
            has_more_before: boolean;
            has_more_after: boolean;
            next_before_cursor: string | null;
            source: string;
            retention_policy_version?: string;
            has_gaps: boolean;
            gap_count: number;
            fallback: boolean;
            points: components["schemas"]["FlowHistoryPoint"][];
        };
        DataCoverageItem: {
            instrument: string;
            timeframe: string;
            rows: number;
            first_ts: components["schemas"]["UnixSeconds"] | null;
            last_ts: components["schemas"]["UnixSeconds"] | null;
        };
        DataCoverageResponse: {
            items: components["schemas"]["DataCoverageItem"][];
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    getOperationsSummary: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Public operations summary */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperationsSummary"];
                };
            };
        };
    };
    getOperationsTrends: {
        parameters: {
            query?: {
                window?: "1h" | "6h" | "24h";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Minute-bucketed local trend history */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OperationsTrends"];
                };
            };
        };
    };
    getMicrostructureHealth: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Read-only health summary */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MicrostructureHealth"];
                };
            };
        };
    };
    getMicrostructureCoverage: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Coverage summary */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CoverageSummary"];
                };
            };
        };
    };
    getMicrostructureEligibility: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Eligibility summary */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EligibilitySummary"];
                };
            };
        };
    };
    getFlowHistory: {
        parameters: {
            query: {
                instrument: string;
                series: "cvd" | "oi";
                timeframe: "1m" | "5m" | "15m" | "1h" | "4h" | "1D";
                start?: components["schemas"]["UnixSeconds"];
                end?: components["schemas"]["UnixSeconds"];
                max_points?: number;
                cursor?: string;
                cvd_mode?: "CONTINUOUS" | "UTC_DAILY_RESET";
                schema_version: "canonical-microstructure-schema-v1";
                history_version: "canonical-microstructure-history-v1";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Flow history contract; the array field is points, never data */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FlowHistoryResponse"];
                };
            };
        };
    };
    getDataCoverage: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Research data coverage */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DataCoverageResponse"];
                };
            };
        };
    };
    getMarketAnalysisContextV2: {
        parameters: {
            query: {
                /** @example ETH-USDT-SWAP */
                instrument: string;
                as_of?: components["schemas"]["UnixSeconds"];
                execution_timeframe?: components["schemas"]["MarketTimeframeV2"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description MarketAnalysisContextV2 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MarketAnalysisContextV2"];
                };
            };
            /** @description Invalid bounded query */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        error: string;
                    };
                };
            };
        };
    };
    getMarketStateV2: {
        parameters: {
            query: {
                instrument: string;
                as_of?: components["schemas"]["UnixSeconds"];
                execution_timeframe?: components["schemas"]["MarketTimeframeV2"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description MarketStateSnapshotV2 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MarketStateSnapshotV2"];
                };
            };
            /** @description Invalid bounded query */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    compareMarketStatesV2: {
        parameters: {
            query: {
                instrument: string;
                previous_as_of: components["schemas"]["UnixSeconds"];
                current_as_of: components["schemas"]["UnixSeconds"];
                execution_timeframe?: components["schemas"]["MarketTimeframeV2"];
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Two snapshots and their deterministic transitions */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MarketStateComparisonV2"];
                };
            };
            /** @description Invalid bounded comparison */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    getStrategyRouteV2: {
        parameters: {
            query: {
                instrument: string;
                as_of?: components["schemas"]["UnixSeconds"];
                previous_as_of?: components["schemas"]["UnixSeconds"];
                execution_timeframe?: "15m";
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description StrategyRouteSnapshotV2 research-only route */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StrategyRouteSnapshotV2"];
                };
            };
            /** @description Invalid bounded query */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    evaluateStrategyRouteFixtureV2: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": {
                    context: components["schemas"]["MarketAnalysisContextV2"];
                    state: components["schemas"]["MarketStateSnapshotV2"];
                    previous_route?: components["schemas"]["StrategyRouteSnapshotV2"] | null;
                };
            };
        };
        responses: {
            /** @description StrategyRouteSnapshotV2 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StrategyRouteSnapshotV2"];
                };
            };
            /** @description Invalid fixture */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Fixture API disabled */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
}
