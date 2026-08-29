"""Unit tests for the untrusted browser boundary of the Superset extension."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parents[1]
    / "integrations"
    / "superset-extension"
    / "backend"
    / "src"
    / "agentbi"
    / "insight_pilot"
    / "validation.py"
)
_SPEC = importlib.util.spec_from_file_location("insight_pilot_validation", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
sanitize_context = _MODULE.sanitize_context

_CONTEXT_BRIDGE_PATH = (
    Path(__file__).parents[1]
    / "integrations"
    / "superset-extension"
    / "frontend"
    / "src"
    / "supersetContextBridge.ts"
)


def valid_context() -> dict:
    return {
        "dashboard_id": "sales-overview",
        "semantic_model_id": 1,
        "time_range": "最近7天",
        "filters": [{"field": "region", "operator": "EQ", "value": "华东"}],
    }


def test_sanitize_context_accepts_minimal_bounded_context() -> None:
    result = sanitize_context(valid_context())

    assert result["dashboard_id"] == "sales-overview"
    assert result["filters"][0]["value"] == "华东"
    assert result["selected"] is None


def test_sanitize_context_rejects_unknown_fields() -> None:
    context = valid_context()
    context["raw_sql"] = "select secret"

    with pytest.raises(ValueError, match="unsupported fields"):
        sanitize_context(context)


def test_sanitize_context_rejects_nested_filter_objects() -> None:
    context = valid_context()
    context["filters"][0]["value"] = {"unexpected": "object"}

    with pytest.raises(TypeError, match="scalar"):
        sanitize_context(context)


def test_sanitize_context_rejects_non_positive_model_id() -> None:
    context = valid_context()
    context["semantic_model_id"] = 0

    with pytest.raises(ValueError, match="positive integer"):
        sanitize_context(context)


def test_chart_selection_targets_only_the_embedding_parent_origin() -> None:
    bridge = _CONTEXT_BRIDGE_PATH.read_text(encoding="utf-8")

    assert "new URL(document.referrer).origin" in bridge
    assert "window.parent.postMessage" in bridge
    assert "}, targetOrigin);" in bridge
    assert "}, '*');" not in bridge
