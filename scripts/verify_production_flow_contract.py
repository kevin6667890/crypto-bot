"""Read-only production verification for UTC-reset CVD and continuous OI."""
from __future__ import annotations

import argparse
import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen


def fetch(base: str, **query):
    url = f"{base.rstrip('/')}/api/paper/flow/history/v1?{urlencode(query)}"
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    args = parser.parse_args()
    now = int(time.time())
    midnight = now - now % 86_400
    common = {
        "instrument": "BTC-USDT",
        "start": midnight - 3_600,
        "end": now,
        "max_points": 500,
        "timeframe": "1m",
        "schema_version": "canonical-microstructure-schema-v2",
        "history_version": "canonical-microstructure-history-v2",
    }
    cvd = fetch(
        args.base_url, **common, series="cvd",
        cvd_mode="UTC_DAILY_RESET")
    current_day = [
        point for point in cvd["points"] if point["time"] >= midnight]
    partial_start = current_day[len(current_day) // 2]["time"]
    partial = fetch(
        args.base_url, **{**common, "start": partial_start},
        series="cvd", cvd_mode="UTC_DAILY_RESET")
    full_by_time = {point["time"]: point for point in cvd["points"]}
    page_consistent = all(
        full_by_time[point["time"]].get("value") == point.get("value")
        and full_by_time[point["time"]].get("delta") == point.get("delta")
        for point in partial["points"])
    first = next(point for point in current_day if point.get("value") is not None)
    oi = fetch(args.base_url, **common, series="oi")
    oi_boundary = [
        point for point in oi["points"]
        if midnight - 900 <= point["time"] <= midnight + 900]
    print(json.dumps({
        "cvd_mode": cvd.get("cvd_mode"),
        "first_utc_bucket_time": first["time"],
        "first_utc_bucket_value": first.get("value"),
        "first_utc_bucket_delta": first.get("delta"),
        "first_bucket_is_real_delta": first.get("value") == first.get("delta"),
        "pagination_consistent": page_consistent,
        "requested_actual_match": (
            cvd.get("requested_resolution") == cvd.get("actual_resolution") == "1m"),
        "canonical_version": cvd.get("canonical_version"),
        "coverage": cvd.get("coverage"),
        "gaps": cvd.get("gaps"),
        "oi_cvd_mode": oi.get("cvd_mode"),
        "oi_boundary_values": [
            {"time": point["time"], "value": point.get("value")}
            for point in oi_boundary],
        "oi_nonzero_across_midnight": bool(oi_boundary) and all(
            point.get("value") not in (None, 0) for point in oi_boundary),
        "cvd_points": len(cvd["points"]),
        "oi_points": len(oi["points"]),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
