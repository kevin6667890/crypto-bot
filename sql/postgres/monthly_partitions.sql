-- Pre-create partitions before enabling a writer. There is intentionally no
-- DEFAULT partition: a missing month fails visibly instead of hiding bad time
-- routing. The function accepts only this file's allow-listed parent tables.

CREATE OR REPLACE FUNCTION crypto_bot.ensure_monthly_partition(
    parent_name text,
    month_start date
) RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    allowed_parents constant text[] := ARRAY[
        'trade_flow_observations',
        'oi_observations',
        'price_observations',
        'funding_observations',
        'liquidation_observations',
        'cvd_aggregates',
        'oi_aggregates'
    ];
    normalized_start date := date_trunc('month', month_start)::date;
    normalized_end date := (date_trunc('month', month_start)
                            + interval '1 month')::date;
    child_name text;
BEGIN
    IF NOT parent_name = ANY(allowed_parents) THEN
        RAISE EXCEPTION 'partition parent % is not allow-listed', parent_name;
    END IF;
    child_name := format(
        '%s_%s', parent_name, to_char(normalized_start, 'YYYY_MM')
    );
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS crypto_bot.%I '
        'PARTITION OF crypto_bot.%I FOR VALUES FROM (%L) TO (%L)',
        child_name,
        parent_name,
        normalized_start::timestamp AT TIME ZONE 'UTC',
        normalized_end::timestamp AT TIME ZONE 'UTC'
    );
    RETURN child_name;
END
$$;

-- Example deterministic preparation. Replace dates during deployment and keep
-- at least the current and next month ready:
-- SELECT crypto_bot.ensure_monthly_partition(
--     'trade_flow_observations', DATE '2026-07-01'
-- );
-- SELECT crypto_bot.ensure_monthly_partition(
--     'trade_flow_observations', DATE '2026-08-01'
-- );
