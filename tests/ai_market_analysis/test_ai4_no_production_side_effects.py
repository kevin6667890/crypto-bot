from pathlib import Path
def test_no_forbidden_production_mutations():
    files=list(Path("dashboard/ai_market_analysis").glob("*.py"));text="\n".join(p.read_text(encoding="utf-8") for p in files)
    for forbidden in ("INSERT INTO paper_trades","UPDATE paper_trades","DELETE FROM paper_trades","create_order(","strategy_router.route(","VACUUM"):
        assert forbidden not in text
def test_legacy_ai_paths_unchanged_by_ai4_commit():
    import subprocess
    diff=subprocess.run(["git","diff","b99e4dccc5ec8b782501d09ef18b022a838426c7","--","dashboard/paper_api.py"],capture_output=True,text=True,check=True).stdout
    assert "def _ai_context" not in diff and "def _create_ai_brief" not in diff and "def chat" not in diff
