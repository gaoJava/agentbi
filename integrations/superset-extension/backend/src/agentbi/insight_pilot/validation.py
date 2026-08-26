"""Pure request validation helpers for the Superset extension boundary."""

from __future__ import annotations

from typing import Any

_CONTEXT_FIELDS = {
    "dashboard_id",
    "chart_id",
    "dataset_id",
    "semantic_model_id",
    "time_range",
    "filters",
    "selected",
}
_FILTER_FIELDS = {"field", "operator", "value"}
_SELECTED_FIELDS = {"label", "value", "dimension"}
_OPERATORS = {"EQ", "IN", "GTE", "LTE"}


def _bounded_text(value: Any, name: str, maximum: int, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} has an invalid length")
    return normalized


def _scalar(value: Any, name: str) -> str | int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError(f"{name} must be a scalar")
    if isinstance(value, str) and len(value) > 512:
        raise ValueError(f"{name} is too long")
    return value


def sanitize_context(raw: Any) -> dict[str, Any]:
    """Return a bounded allowlisted dashboard context or raise ``ValueError``."""

    if not isinstance(raw, dict) or set(raw) - _CONTEXT_FIELDS:
        raise ValueError("context contains unsupported fields")

    dashboard_id = _bounded_text(raw.get("dashboard_id"), "dashboard_id", 128)
    time_range = _bounded_text(raw.get("time_range"), "time_range", 256)
    semantic_model_id = raw.get("semantic_model_id")
    if (
        isinstance(semantic_model_id, bool)
        or not isinstance(semantic_model_id, int)
        or semantic_model_id <= 0
    ):
        raise ValueError("semantic_model_id must be a positive integer")

    filters = raw.get("filters", [])
    if not isinstance(filters, list) or len(filters) > 50:
        raise ValueError("filters must contain at most 50 items")
    clean_filters: list[dict[str, Any]] = []
    for item in filters:
        if not isinstance(item, dict) or set(item) != _FILTER_FIELDS:
            raise ValueError("filter fields are invalid")
        operator = item.get("operator")
        if operator not in _OPERATORS:
            raise ValueError("filter operator is invalid")
        value = item.get("value")
        if isinstance(value, list):
            if len(value) > 100:
                raise ValueError("filter value list is too large")
            clean_value: Any = [_scalar(entry, "filter value") for entry in value]
        else:
            clean_value = _scalar(value, "filter value")
        clean_filters.append(
            {
                "field": _bounded_text(item.get("field"), "filter field", 128),
                "operator": operator,
                "value": clean_value,
            }
        )

    selected = raw.get("selected")
    clean_selected: dict[str, Any] | None = None
    if selected is not None:
        if not isinstance(selected, dict) or set(selected) - _SELECTED_FIELDS:
            raise ValueError("selected datum fields are invalid")
        value = selected.get("value")
        clean_selected = {
            "label": _bounded_text(selected.get("label"), "selected label", 256),
            "value": None if value is None else _scalar(value, "selected value"),
            "dimension": _bounded_text(
                selected.get("dimension"), "selected dimension", 128, required=False
            ),
        }

    return {
        "dashboard_id": dashboard_id,
        "chart_id": _bounded_text(raw.get("chart_id"), "chart_id", 128, required=False),
        "dataset_id": _bounded_text(raw.get("dataset_id"), "dataset_id", 128, required=False),
        "semantic_model_id": semantic_model_id,
        "time_range": time_range,
        "filters": clean_filters,
        "selected": clean_selected,
    }
