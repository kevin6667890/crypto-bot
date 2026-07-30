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
            status: "VALID" | "WHITESPACE" | "PARTIAL_AFTER_GAP" | "ARCHIVED_CONFIRMED";
            gap_reason?: string | null;
            source_complete: boolean;
            partial_after_gap: boolean;
        };
        FlowHistoryResponse: {
            api_version: string;
            instrument: string;
            /** @enum {string} */
            series: "cvd" | "oi";
            /** @enum {string|null} */
            cvd_mode?: "CONTINUOUS" | "UTC_DAILY_RESET" | null;
            requested_start: components["schemas"]["UnixSeconds"];
            requested_end: components["schemas"]["UnixSeconds"];
            available_start: components["schemas"]["UnixSeconds"] | null;
            available_end: components["schemas"]["UnixSeconds"] | null;
            latest_timestamp: components["schemas"]["UnixSeconds"] | null;
            raw_row_count: number;
            returned_point_count: number;
            resolution: string | null;
            resolution_seconds: number | null;
            stale: boolean;
            has_history: boolean;
            has_more_before: boolean;
            has_more_after: boolean;
            next_before_cursor: string | null;
            source: string;
            retention_policy_version: string;
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
                start?: components["schemas"]["UnixSeconds"];
                end?: components["schemas"]["UnixSeconds"];
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
}
