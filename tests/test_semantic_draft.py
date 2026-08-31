from agentbi.semantic_draft import build_semantic_draft


def test_builds_reviewable_metadata_draft_without_claiming_ai() -> None:
    draft = build_semantic_draft({
        "superset_id": 21, "name": "sales_orders", "schema": "public",
        "database_name": "examples",
        "columns": [
            {"name": "order_id", "type": "BIGINT", "is_time": False},
            {"name": "order_date", "type": "DATE", "is_time": True},
            {"name": "region", "type": "VARCHAR", "is_time": False},
            {"name": "channel", "type": "VARCHAR", "is_time": False},
            {"name": "revenue", "type": "NUMERIC", "is_time": False},
        ],
    })

    assert draft["generation"] == {
        "source": "metadata_inference", "ai_generated": False,
        "label": "字段元数据推断（未配置 LLM）",
    }
    assert draft["identifiers"][0]["field"] == "order_id"
    assert draft["dimensions"][0]["type"] == "time"
    assert draft["measures"][0]["field"] == "revenue"
    assert draft["model"]["name"] == "销售订单"
    assert draft["model"]["biz_name"] == "sales_orders_model"
    assert draft["drilldown_path"] == ["区域", "渠道"]
    assert draft["warnings"] == []
