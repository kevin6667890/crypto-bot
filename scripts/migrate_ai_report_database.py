from __future__ import annotations
import argparse,json
from dashboard.ai_market_analysis.report_migrations import apply_migrations,manifest_sha256

def main()->int:
    p=argparse.ArgumentParser(description="Explicitly apply verified AI report migrations 001-004")
    p.add_argument("--database",required=True)
    p.add_argument("--expected-manifest-sha256",required=True)
    a=p.parse_args()
    actual=manifest_sha256()
    if a.expected_manifest_sha256.lower()!=actual:
        p.error("MIGRATION_MANIFEST_APPROVAL_MISMATCH")
    print(json.dumps(apply_migrations(a.database),sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())
