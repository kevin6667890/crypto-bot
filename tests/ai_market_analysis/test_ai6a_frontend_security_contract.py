from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "frontend" / "src" / "aiMarketAnalysis"


def test_shadow_sources_have_no_unsafe_rendering_or_code_execution():
    source = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.tsx"))
    assert "dangerouslySetInnerHTML" not in source
    assert "new Function" not in source
    assert "eval(" not in source


def test_admin_token_is_memory_only_and_not_logged():
    source = (ROOT / "ShadowMarketAnalysisPage.tsx").read_text(encoding="utf-8")
    assert "localStorage" not in source and "sessionStorage" not in source
    assert "console." not in source and "token=" not in source


def test_position_sensitive_fields_are_not_in_initial_panel_contract():
    source = (ROOT / "ShadowMarketAnalysisPage.tsx").read_text(encoding="utf-8")
    api = (ROOT / "api.ts").read_text(encoding="utf-8")
    assert "/position" in api
    assert "fetchPositionDetails" in source


def test_presentation_projection_has_no_network_or_market_database_dependency():
    source = (Path(__file__).resolve().parents[2] / "dashboard" / "ai_market_analysis" / "presentation.py").read_text(encoding="utf-8")
    for forbidden in ("urlopen", "requests.", "httpx.", "paper_trades", "microstructure", "raw_trades", "raw_oi"):
        assert forbidden not in source
