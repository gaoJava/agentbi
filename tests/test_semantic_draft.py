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


def test_reserved_ranking_column_is_not_modeled_as_a_measure_or_dimension() -> None:
    draft = build_semantic_draft({
        "superset_id": 20, "name": "video_game_sales", "schema": "public",
        "database_name": "examples",
        "columns": [
            {"name": "year", "type": "BIGINT", "is_time": True},
            {"name": "rank", "type": "BIGINT", "is_time": False},
            {"name": "publisher", "type": "VARCHAR", "is_time": False},
            {"name": "global_sales", "type": "FLOAT", "is_time": False},
        ],
    })
    modeled_fields = {
        item["field"] for section in ("identifiers", "dimensions", "measures")
        for item in draft[section]
    }
    assert "rank" not in modeled_fields
    assert next(item for item in draft["dimensions"] if item["field"] == "year")["type"] == "time"
