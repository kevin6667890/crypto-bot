from dashboard.ai_market_analysis.presentation_narrative import (
    PRESENTATION_NARRATIVE_VERSION,
    project_display_narrative,
)


def test_display_projection_is_post_audit_only_and_keeps_persisted_source_unchanged():
    source = {
        "audit_status": "PENDING", "headline": "HH_HL 延续",
        "sections": [
            {"section_id": "CONCLUSION", "body": "而中周期维持HH_HL结构。"},
            {"section_id": "LIMITATIONS", "body": "订单流不可用。报告未经审计，审计状态为待定。"},
            {"section_id": "TF_4H", "body": "摆动结构为LH_LL。"},
        ],
    }
    displayed = project_display_narrative(source, audit_status="PASSED")
    assert source["audit_status"] == "PENDING"
    assert source["sections"][0]["body"] == "而中周期维持HH_HL结构。"
    assert displayed["audit_status"] == "PASSED"
    assert displayed["presentation_narrative_version"] == PRESENTATION_NARRATIVE_VERSION
    assert displayed["headline"] == "更高高点 / 更高低点 延续"
    assert displayed["sections"][0]["body"] == "综合来看，中周期维持更高高点 / 更高低点结构。"
    assert displayed["sections"][1]["body"] == "订单流不可用。"
    assert displayed["sections"][2]["body"] == "摆动结构为更低高点 / 更低低点。"


def test_pending_projection_does_not_claim_a_completed_audit():
    source = {"audit_status": "PENDING", "sections": [
        {"section_id": "LIMITATIONS", "body": "报告未经审计，审计状态为待定。"},
    ]}
    displayed = project_display_narrative(source, audit_status="PENDING")
    assert displayed["audit_status"] == "PENDING"
    assert "未经审计" in displayed["sections"][0]["body"]
