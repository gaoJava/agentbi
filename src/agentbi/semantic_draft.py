"""Build reviewable semantic-model drafts from real Superset Dataset metadata.

The metadata inference in this module is deliberately deterministic and is never
labelled as an LLM result.  It provides a safe fallback and a stable contract for
an optional LLM provider to enrich later.
"""

from __future__ import annotations

import re
from typing import Any

_NUMERIC = re.compile(r"(int|long|float|double|decimal|numeric|number|real)", re.IGNORECASE)
_IDENTIFIER = re.compile(r"(^id$|_id$|^.*key$|^.*code$)", re.IGNORECASE)
_LABELS = {
    "id": "编号",
    "name": "名称",
    "date": "日期",
    "time": "时间",
    "year": "年份",
    "month": "月份",
    "region": "区域",
    "country": "国家",
    "city": "城市",
    "category": "类别",
    "product": "产品",
    "customer": "客户",
    "user": "用户",
    "department": "部门",
    "sales": "销售额",
    "revenue": "收入",
    "amount": "金额",
    "profit": "利润",
    "cost": "成本",
    "quantity": "数量",
    "count": "数量",
    "price": "价格",
    "status": "状态",
    "channel": "渠道",
    "video": "视频",
    "game": "游戏",
    "genre": "类型",
    "platform": "平台",
    "publisher": "发行商",
    "na": "北美",
    "eu": "欧洲",
    "jp": "日本",
    "global": "全球",
    "other": "其他",
    "rank": "排名",
}
_DATASET_LABELS = {
    "sales_orders": "销售订单",
    "video_game_sales": "视频游戏销量",
}


def _display_name(value: str) -> str:
    tokens = [token for token in re.split(r"[_\-\s]+", value.strip()) if token]
    translated = [_LABELS.get(token.lower(), token) for token in tokens]
    return "".join(translated)[:128] or value[:128]


def _synonyms(name: str, display_name: str) -> list[str]:
    values = [display_name]
    spaced = " ".join(filter(None, re.split(r"[_\-]+", name)))
    if spaced and spaced.lower() != name.lower():
        values.append(spaced)
    return list(dict.fromkeys(value[:64] for value in values if value))[:5]


def _model_biz_name(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_") or "semantic"
    if not base[0].isalpha():
        base = f"model_{base}"
    return f"{base[:122]}_model"


def build_semantic_draft(dataset: dict[str, Any]) -> dict[str, Any]:
    """Infer an editable draft from the sanitized Superset Dataset response."""

    columns = [column for column in dataset.get("columns", []) if column.get("name")]
    identifiers: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    fields: list[dict[str, str]] = []
    for column in columns:
        name = str(column["name"])[:128]
        data_type = str(column.get("type") or "Unknown")[:64]
        label = _display_name(name)
        fields.append({"name": name, "type": data_type})
        if name.lower() in {"rank", "row_number"}:
            continue
        if _IDENTIFIER.search(name) and not identifiers:
            identifiers.append(
                {
                    "name": label,
                    "field": name,
                    "type": "primary",
                    "synonyms": _synonyms(name, label),
                }
            )
            continue
        if bool(column.get("is_time")) or re.search(
            r"(date|time|timestamp)", data_type, re.IGNORECASE
        ):
            dimensions.append(
                {
                    "name": label,
                    "field": name,
                    "type": "time",
                    "synonyms": _synonyms(name, label),
                }
            )
        elif _NUMERIC.search(data_type):
            measures.append(
                {
                    "name": f"总{label}",
                    "field": name,
                    "aggregation": "SUM",
                    "synonyms": _synonyms(name, label),
                }
            )
        else:
            dimensions.append(
                {
                    "name": label,
                    "field": name,
                    "type": "categorical",
                    "synonyms": _synonyms(name, label),
                }
            )

    if not identifiers and columns:
        first = str(columns[0]["name"])[:128]
        label = _display_name(first)
        identifiers.append(
            {
                "name": label,
                "field": first,
                "type": "primary",
                "synonyms": _synonyms(first, label),
            }
        )

    categorical = [item["name"] for item in dimensions if item["type"] == "categorical"]
    warnings: list[str] = []
    if not measures:
        warnings.append("未识别到数值字段，请人工配置度量后再发布。")
    if len(categorical) < 2:
        warnings.append("可下钻的分类维度少于两层，请人工确认下钻路径。")
    dataset_name = str(dataset.get("name") or "dataset")[:128]
    return {
        "dataset": {
            "superset_id": int(dataset["superset_id"]),
            "name": dataset_name,
            "schema": str(dataset.get("schema") or ""),
            "database_name": str(dataset.get("database_name") or ""),
        },
        "model": {
            "name": _DATASET_LABELS.get(dataset_name.lower(), _display_name(dataset_name)),
            "biz_name": _model_biz_name(dataset_name),
            "description": f"基于 Superset Dataset {dataset_name} 生成的待审核语义草稿",
        },
        "identifiers": identifiers[:5],
        "dimensions": dimensions[:50],
        "measures": measures[:50],
        "fields": fields[:200],
        "drilldown_path": categorical[:5],
        "generation": {
            "source": "metadata_inference",
            "ai_generated": False,
            "label": "字段元数据推断（未配置 LLM）",
        },
        "warnings": warnings,
    }
