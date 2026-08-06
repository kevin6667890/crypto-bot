"""Bounded query-only SQLite adapter for canonical microstructure aggregates."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .versions import ORDERFLOW_RESOLUTIONS, SUPPORTED_INSTRUMENTS

TABLES={"cvd":"cvd_aggregates","oi":"oi_aggregates","basis":"basis_aggregates"}


class ReadOnlyOrderflowAdapter:
    def __init__(self,path:Path|str):
        self.path=Path(path); self.query_plans=[]

    def read(self,instrument:str,start:int,end:int,resolution:str="15m") -> dict[str,list[dict[str,Any]]]:
        if instrument not in SUPPORTED_INSTRUMENTS: raise ValueError("unsupported instrument")
        if resolution not in ORDERFLOW_RESOLUTIONS: raise ValueError("unsupported resolution")
        if end<=start or end-start>366*86400: raise ValueError("query range must be positive and bounded to 366 days")
        uri=f"file:{self.path.resolve().as_posix()}?mode=ro"
        output={}
        with sqlite3.connect(uri,uri=True) as connection:
            connection.row_factory=sqlite3.Row; connection.execute("PRAGMA query_only=ON")
            for source,table in TABLES.items():
                if not _exists(connection,table): output[source]=[]; continue
                sql=f"SELECT * FROM {table} WHERE instrument=? AND resolution=? AND bucket_ms>=? AND bucket_ms<? ORDER BY bucket_ms"
                params=(instrument,resolution,start*1000,end*1000)
                plan=[tuple(row) for row in connection.execute("EXPLAIN QUERY PLAN "+sql,params)]
                self._assert_indexed(plan,table); self.query_plans.extend(plan)
                rows=[]
                for row in connection.execute(sql,params):
                    item=dict(row)
                    if "payload_json" in item:
                        payload=json.loads(item.pop("payload_json")); item.update(payload)
                    rows.append(item)
                output[source]=rows
            output["funding"]=[]
            for table,state in (("funding_settled","SETTLED"),("funding_predicted","PREDICTED")):
                if not _exists(connection,table): continue
                sql=f"SELECT source_ts_ms,funding_rate,state FROM {table} WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<? ORDER BY source_ts_ms"
                params=(instrument,start*1000,end*1000); plan=[tuple(row) for row in connection.execute("EXPLAIN QUERY PLAN "+sql,params)]
                self._assert_indexed(plan,table); self.query_plans.extend(plan)
                output["funding"].extend({"timestamp":int(row["source_ts_ms"])//1000,"rate":float(row["funding_rate"]),
                                          "state":state,"source_type":state,"source_state":row["state"]} for row in connection.execute(sql,params))
            output["liquidation"]=[]; output["liquidation_complete"]=False
            if _exists(connection,"liquidation_observations"):
                sql="SELECT source_ts_ms,side,size,price,reliability_note FROM liquidation_observations WHERE instrument=? AND source_ts_ms>=? AND source_ts_ms<? ORDER BY source_ts_ms"
                params=(instrument,start*1000,end*1000); plan=[tuple(row) for row in connection.execute("EXPLAIN QUERY PLAN "+sql,params)]
                self._assert_indexed(plan,"liquidation_observations"); self.query_plans.extend(plan)
                output["liquidation"]=[{"timestamp":int(row["source_ts_ms"])//1000,"side":str(row["side"]).upper(),
                                        "size":float(row["size"]),"notional":float(row["size"])*float(row["price"] or 0),
                                        "reliability_note":row["reliability_note"]} for row in connection.execute(sql,params)]
        return output

    @staticmethod
    def _assert_indexed(plan,table):
        detail=" ".join(str(row[-1]).upper() for row in plan)
        if f"SCAN {table.upper()}" in detail and "USING INDEX" not in detail and "USING COVERING INDEX" not in detail:
            raise RuntimeError(f"unbounded/full table scan rejected: {detail}")


def _exists(connection,table):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None
