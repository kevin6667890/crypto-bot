-- Crypto-Bot PostgreSQL storage prototype.
-- Apply as an application owner to an empty non-production database.

BEGIN;

CREATE SCHEMA IF NOT EXISTS crypto_bot;

CREATE TABLE IF NOT EXISTS crypto_bot.trade_flow_observations (
    observed_at timestamptz NOT NULL,
    source_ts_ms bigint NOT NULL,
    source text NOT NULL,
    source_version text NOT NULL,
    instrument text NOT NULL,
    ingested_at timestamptz NOT NULL,
    ingested_at_ms bigint NOT NULL,
    resolution text NOT NULL,
    state text NOT NULL CHECK (state IN ('confirmed', 'provisional')),
    source_identity text NOT NULL,
    uniqueness_key text NOT NULL,
    trade_id text,
    side text NOT NULL CHECK (side IN ('buy', 'sell')),
    price double precision NOT NULL,
    size double precision NOT NULL,
    contract_value double precision NOT NULL,
    notional double precision NOT NULL,
    provenance_table text,
    PRIMARY KEY (observed_at, uniqueness_key)
) PARTITION BY RANGE (observed_at);

CREATE INDEX IF NOT EXISTS trade_flow_instrument_time_idx
    ON crypto_bot.trade_flow_observations
       (instrument, observed_at, uniqueness_key);

CREATE INDEX IF NOT EXISTS trade_flow_ingested_at_idx
    ON crypto_bot.trade_flow_observations (ingested_at);

CREATE TABLE IF NOT EXISTS crypto_bot.oi_observations (
    observed_at timestamptz NOT NULL,
    source_ts_ms bigint NOT NULL,
    source text NOT NULL,
    source_version text NOT NULL,
    instrument text NOT NULL,
    ingested_at timestamptz NOT NULL,
    ingested_at_ms bigint NOT NULL,
    resolution text NOT NULL,
    state text NOT NULL CHECK (state IN ('confirmed', 'provisional')),
    source_identity text NOT NULL,
    uniqueness_key text NOT NULL,
    oi_contracts double precision,
    oi_currency double precision,
    oi_usd double precision,
    provenance_table text,
    PRIMARY KEY (observed_at, uniqueness_key)
) PARTITION BY RANGE (observed_at);

CREATE INDEX IF NOT EXISTS oi_instrument_time_idx
    ON crypto_bot.oi_observations
       (instrument, observed_at, uniqueness_key);

CREATE TABLE IF NOT EXISTS crypto_bot.price_observations (
    observed_at timestamptz NOT NULL,
    source_ts_ms bigint NOT NULL,
    price_kind text NOT NULL CHECK (price_kind IN ('mark', 'index')),
    source text NOT NULL,
    source_version text NOT NULL,
    instrument text NOT NULL,
    ingested_at timestamptz NOT NULL,
    ingested_at_ms bigint NOT NULL,
    resolution text NOT NULL,
    state text NOT NULL CHECK (state IN ('confirmed', 'provisional')),
    source_identity text NOT NULL,
    uniqueness_key text NOT NULL,
    open double precision,
    high double precision,
    low double precision,
    close double precision NOT NULL,
    PRIMARY KEY (observed_at, price_kind, uniqueness_key)
) PARTITION BY RANGE (observed_at);

CREATE INDEX IF NOT EXISTS price_instrument_time_idx
    ON crypto_bot.price_observations
       (instrument, price_kind, observed_at, uniqueness_key);

CREATE TABLE IF NOT EXISTS crypto_bot.funding_observations (
    observed_at timestamptz NOT NULL,
    source_ts_ms bigint NOT NULL,
    funding_kind text NOT NULL CHECK (funding_kind IN ('settled', 'predicted')),
    source text NOT NULL,
    source_version text NOT NULL,
    instrument text NOT NULL,
    ingested_at timestamptz NOT NULL,
    ingested_at_ms bigint NOT NULL,
    resolution text NOT NULL,
    state text NOT NULL CHECK (state IN ('confirmed', 'provisional')),
    source_identity text NOT NULL,
    uniqueness_key text NOT NULL,
    funding_rate double precision NOT NULL,
    realized_rate double precision,
    funding_time_ms bigint,
    next_funding_time_ms bigint,
    premium double precision,
    PRIMARY KEY (observed_at, funding_kind, uniqueness_key),
    CHECK (
        (funding_kind = 'settled' AND funding_time_ms IS NOT NULL)
        OR
        (funding_kind = 'predicted' AND next_funding_time_ms IS NOT NULL)
    )
) PARTITION BY RANGE (observed_at);

CREATE INDEX IF NOT EXISTS funding_instrument_time_idx
    ON crypto_bot.funding_observations
       (instrument, funding_kind, observed_at, uniqueness_key);

CREATE TABLE IF NOT EXISTS crypto_bot.liquidation_observations (
    observed_at timestamptz NOT NULL,
    source_ts_ms bigint NOT NULL,
    source text NOT NULL,
    source_version text NOT NULL,
    instrument text NOT NULL,
    ingested_at timestamptz NOT NULL,
    ingested_at_ms bigint NOT NULL,
    resolution text NOT NULL,
    state text NOT NULL CHECK (state IN ('confirmed', 'provisional')),
    source_identity text NOT NULL,
    uniqueness_key text NOT NULL,
    side text NOT NULL,
    size double precision NOT NULL,
    price double precision,
    bankruptcy_loss double precision,
    reliability_note text NOT NULL,
    PRIMARY KEY (observed_at, uniqueness_key)
) PARTITION BY RANGE (observed_at);

CREATE INDEX IF NOT EXISTS liquidation_instrument_time_idx
    ON crypto_bot.liquidation_observations
       (instrument, observed_at, uniqueness_key);

-- CVD and OI retain separate columns and units.  They are not folded into a
-- generic metric/value pair, which prevents semantic reinterpretation.
CREATE TABLE IF NOT EXISTS crypto_bot.cvd_aggregates (
    bucket_at timestamptz NOT NULL,
    bucket_ms bigint NOT NULL,
    instrument text NOT NULL,
    resolution text NOT NULL,
    buy_notional double precision NOT NULL,
    sell_notional double precision NOT NULL,
    delta double precision NOT NULL,
    cumulative_anchored double precision NOT NULL,
    observation_count bigint NOT NULL,
    first_source_ts_ms bigint NOT NULL,
    last_source_ts_ms bigint NOT NULL,
    gap_flag boolean NOT NULL,
    source_version text NOT NULL,
    PRIMARY KEY (bucket_at, instrument, resolution)
) PARTITION BY RANGE (bucket_at);

CREATE TABLE IF NOT EXISTS crypto_bot.oi_aggregates (
    bucket_at timestamptz NOT NULL,
    bucket_ms bigint NOT NULL,
    instrument text NOT NULL,
    resolution text NOT NULL,
    first_value double precision NOT NULL,
    last_value double precision NOT NULL,
    min_value double precision NOT NULL,
    max_value double precision NOT NULL,
    absolute_change double precision NOT NULL,
    percentage_change double precision,
    observation_count bigint NOT NULL,
    first_source_ts_ms bigint NOT NULL,
    last_source_ts_ms bigint NOT NULL,
    gap_flag boolean NOT NULL,
    source_version text NOT NULL,
    PRIMARY KEY (bucket_at, instrument, resolution)
) PARTITION BY RANGE (bucket_at);

COMMIT;
