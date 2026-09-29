"""Strict confirmed-bar alignment; never forward/back fills cross-asset inputs."""
from __future__ import annotations
def align_confirmed(primary, context):
    by_ts={int(x['ts']):x for x in context if x.get('confirmed',1)}; out=[]
    for row in primary:
        ts=int(row['ts'])
        if not row.get('confirmed',1) or ts not in by_ts: continue
        out.append((row,by_ts[ts]))
    return out
