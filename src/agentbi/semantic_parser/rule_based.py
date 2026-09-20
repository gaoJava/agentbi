"""Small deterministic parser for the Phase 2 analysis-request contract.

This parser records user intent and unresolved business language.  It neither
chooses an analysis strategy nor creates an executable query plan.
"""

from __future__ import annotations

import re

from agentbi.ontology_service.contracts import OntologyAsset, OntologyAssetKind, OntologyService
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    AnalysisSignal,
    AnalysisTarget,
    AnalysisTargetKind,
    ComparisonSpec,
    ComparisonType,
    RelativeTimeReference,
    ResolutionStatus,
    SemanticOrdering,
    SemanticQueryIR,
    SemanticScope,
    SignalDirection,
    SortDirection,
    TimeScope,
    TimeScopeKind,
    ValueSource,
)

from .contracts import Clarification, ParseRequest, ParseResult

_DEFAULT_METRICS = ("销售额", "营收", "收入", "利润")
_DEFAULT_DIMENSIONS = ("渠道", "区域", "地区", "产品", "客户")
_REGION_VALUES = ("华东", "华南", "华北", "华中", "西南", "西北", "东北")


class RuleBasedSemanticParser:
    """Ground obvious names through a published snapshot when one is available.

    The lexical rules are intentionally small and transparent.  Unknown terms
    become unresolved targets instead of guessed ontology IDs; ambiguous names
    preserve all candidate IDs for a later grounding/clarification step.
    """

    def __init__(self, ontology_service: OntologyService | None = None):
        self._ontology_service = ontology_service

    async def parse(self, request: ParseRequest) -> ParseResult:
        assets = await self._published_assets(request.ontology_version)
        target, consumed_metric_spans = self._metric_target(request.question, assets)
        if target is None and request.context and request.context.metric:
            context_target, _ = self._metric_target(request.context.metric, assets)
            target = (context_target or AnalysisTarget(raw_text=request.context.metric, kind=AnalysisTargetKind.METRIC)).model_copy(update={"source":ValueSource.WORKBENCH_CONTEXT})
        if target is None:
            return ParseResult(
                clarification=Clarification(reason="未识别到要分析的指标"),
                confidence=0.0,
            )
        if _requires_time_clarification(request.question) and _time_scope(request.question) is None:
            return ParseResult(clarification=Clarification(reason="请补充分析时间范围"), confidence=0.0)

        scope = self._region_scope(request.question, assets) or self._context_region(request.context, assets)
        breakdown_hints = self._breakdown_hints(request.question, assets, scope, consumed_metric_spans)
        ranking = _ranking(request.question)
        query = SemanticQueryIR(
            ontology_version=request.ontology_version,
            source_question=request.question,
            intent=AnalysisIntent.BREAKDOWN if ranking is not None and breakdown_hints else _intent(request.question),
            targets=[target],
            scopes=[scope] if scope is not None else [],
            time_scope=_time_scope(request.question) or ((_time_scope(request.context.time_range).model_copy(update={"source":ValueSource.WORKBENCH_CONTEXT})) if request.context and request.context.time_range and _time_scope(request.context.time_range) else None),
            signal=_signal(request.question),
            comparison=_comparison(request.question),
            breakdown_hints=breakdown_hints,
            order_by=[SemanticOrdering(target_alias=target.asset_id or target.raw_text,
                                       direction=ranking[0])] if ranking is not None else [],
            limit=ranking[1] if ranking is not None else None,
        )
        return ParseResult(query=query, confidence=_confidence(target))

    @staticmethod
    def _context_region(context, assets):
        if context is None: return None
        for field, value in context.filters:
            if field.casefold() in {"region", "区域", "地区", "客户区域"}:
                return SemanticScope(dimension=_target_from_candidates("区域",AnalysisTargetKind.DIMENSION,_region_dimension_candidates(assets)),value=value,source=ValueSource.WORKBENCH_CONTEXT)
        return None

    async def _published_assets(self, version: str) -> tuple[OntologyAsset, ...]:
        if self._ontology_service is None:
            return ()
        snapshot = await self._ontology_service.published_snapshot(version)
        return snapshot.assets if snapshot is not None else ()

    @staticmethod
    def _metric_target(question: str, assets: tuple[OntologyAsset, ...]) -> tuple[AnalysisTarget | None, tuple[tuple[int, int], ...]]:
        matches = _matching_assets(question, assets, OntologyAssetKind.METRIC)
        if matches:
            raw_text, candidates, start, end = matches[0]
            return _target_from_candidates(raw_text, AnalysisTargetKind.METRIC, candidates), ((start, end),)
        raw_text = _first_present(question, _DEFAULT_METRICS)
        return (
            AnalysisTarget(raw_text=raw_text, kind=AnalysisTargetKind.METRIC)
            if raw_text is not None else None, ()
        )

    @staticmethod
    def _region_scope(question: str, assets: tuple[OntologyAsset, ...]) -> SemanticScope | None:
        value = _first_present(question, _REGION_VALUES)
        if value is None:
            return None
        candidates = _region_dimension_candidates(assets)
        dimension = _target_from_candidates("区域", AnalysisTargetKind.DIMENSION, candidates)
        return SemanticScope(dimension=dimension, value=value)

    @staticmethod
    def _breakdown_hints(
        question: str,
        assets: tuple[OntologyAsset, ...],
        scope: SemanticScope | None, consumed_spans: tuple[tuple[int, int], ...],
    ) -> list[AnalysisTarget]:
        hints: list[AnalysisTarget] = []
        scope_asset_id = scope.dimension.asset_id if scope is not None else None
        for raw_text, candidates, start, end in _matching_assets(question, assets, OntologyAssetKind.DIMENSION):
            if any(start < occupied_end and occupied_start < end for occupied_start, occupied_end in consumed_spans):
                continue
            target = _target_from_candidates(raw_text, AnalysisTargetKind.DIMENSION, candidates)
            if target.asset_id != scope_asset_id and target not in hints:
                hints.append(target)
        if not hints:
            for raw_text in _DEFAULT_DIMENSIONS:
                spans = tuple((match.start(), match.end()) for match in re.finditer(re.escape(raw_text), question))
                if (raw_text in question and raw_text not in {"区域", "地区"}
                        and any(not any(start < occupied_end and occupied_start < end for occupied_start, occupied_end in consumed_spans) for start, end in spans)):
                    hints.append(AnalysisTarget(raw_text=raw_text, kind=AnalysisTargetKind.DIMENSION))
        return hints


def _matching_assets(
    question: str, assets: tuple[OntologyAsset, ...], kind: OntologyAssetKind
) -> list[tuple[str, tuple[OntologyAsset, ...], int, int]]:
    """Return deterministic alias spans, longest first, grouping ambiguity."""

    matches: dict[tuple[str, int, int], list[OntologyAsset]] = {}
    normalized_question = question.casefold()
    for asset in assets:
        if asset.kind is not kind:
            continue
        for label in (asset.name, *asset.aliases):
            normalized_label = label.strip().casefold()
            if normalized_label:
                for found in re.finditer(re.escape(normalized_label), normalized_question):
                    matches.setdefault((label, found.start(), found.end()), []).append(asset)
    return [
        (raw_text, tuple(sorted(candidates, key=lambda candidate: candidate.id)), start, end)
        for (raw_text, start, end), candidates in sorted(matches.items(), key=lambda item: (-len(item[0][0]), item[0][1], item[0][0]))
    ]


def _region_dimension_candidates(assets: tuple[OntologyAsset, ...]) -> tuple[OntologyAsset, ...]:
    return tuple(
        asset for asset in assets
        if asset.kind is OntologyAssetKind.DIMENSION
        and any(term in label.casefold() for label in (asset.name, *asset.aliases)
                for term in ("区域", "地区", "region"))
    )


def _target_from_candidates(
    raw_text: str, kind: AnalysisTargetKind, candidates: tuple[OntologyAsset, ...]
) -> AnalysisTarget:
    unique = {candidate.id: candidate for candidate in candidates}
    if len(unique) == 1:
        return AnalysisTarget(
            raw_text=raw_text, kind=kind, asset_id=next(iter(unique)),
            resolution_status=ResolutionStatus.RESOLVED,
        )
    if len(unique) > 1:
        return AnalysisTarget(
            raw_text=raw_text, kind=kind, resolution_status=ResolutionStatus.AMBIGUOUS,
            candidate_asset_ids=tuple(sorted(unique)),
        )
    return AnalysisTarget(raw_text=raw_text, kind=kind)


def _intent(question: str) -> AnalysisIntent:
    if any(token in question for token in ("为什么", "为何", "什么原因", "原因")):
        return AnalysisIntent.ROOT_CAUSE
    if any(token in question for token in ("下降最多", "下降主要", "下降贡献", "下降受")):
        return AnalysisIntent.ROOT_CAUSE
    if any(token in question for token in ("趋势", "走势")):
        return AnalysisIntent.TREND
    if _comparison(question) is not None:
        return AnalysisIntent.COMPARISON
    if any(token in question for token in ("各", "按", "分", "分别")):
        return AnalysisIntent.BREAKDOWN
    return AnalysisIntent.QUERY


def _signal(question: str) -> AnalysisSignal | None:
    for direction, terms in (
        (SignalDirection.ANOMALY, ("异常波动", "异常", "突增", "突然")),
        (SignalDirection.DECLINE, ("下降", "下跌", "减少", "下滑")),
        (SignalDirection.INCREASE, ("增加", "上涨", "增长", "上升")),
        (SignalDirection.CHANGE, ("变化", "波动", "变动")),
    ):
        raw_text = _first_present(question, terms)
        if raw_text is not None:
            return AnalysisSignal(direction=direction, raw_text=raw_text)
    return None


def _comparison(question: str) -> ComparisonSpec | None:
    explicit_months = re.findall(r"\d{4}年\d{1,2}月", question)
    if len(explicit_months) >= 2 and ("比" in question or "变化" in question):
        return ComparisonSpec(type=ComparisonType.CUSTOM, raw_text=" vs ".join(explicit_months[:2]))
    for comparison_type, terms in (
        (ComparisonType.YOY, ("同比", "去年同期")),
        (ComparisonType.MOM, ("环比", "上个月")),
        (ComparisonType.PREVIOUS_PERIOD, ("上一期", "前一期")),
    ):
        raw_text = _first_present(question, terms)
        if raw_text is not None:
            return ComparisonSpec(type=comparison_type, raw_text=raw_text)
    if "今年" in question and "去年" in question:
        return ComparisonSpec(type=ComparisonType.YOY, raw_text="今年和去年")
    return ComparisonSpec(type=ComparisonType.CUSTOM, raw_text="相比") if "相比" in question else None


def _time_scope(question: str) -> TimeScope | None:
    patterns = (
        (r"最近\s*(\d+)\s*天", RelativeTimeReference.LAST_N_DAYS),
        (r"最近\s*(\d+)\s*周", RelativeTimeReference.LAST_N_WEEKS),
        (r"最近\s*(\d+)\s*(?:个)?月", RelativeTimeReference.LAST_N_MONTHS),
    )
    for pattern, reference in patterns:
        matched = re.search(pattern, question)
        if matched:
            return TimeScope(
                raw_text=matched.group(0), kind=TimeScopeKind.RELATIVE,
                relative_reference=reference, amount=int(matched.group(1)),
            )
    for raw_text, reference in (("昨天", RelativeTimeReference.YESTERDAY), ("本周", RelativeTimeReference.THIS_WEEK), ("最近", RelativeTimeReference.RECENT)):
        if raw_text in question:
            return TimeScope(raw_text=raw_text, kind=TimeScopeKind.RELATIVE,
                             relative_reference=reference)
    matched = re.search(r"\d{4}年(?:\d{1,2}月)?", question)
    if matched:
        return TimeScope(raw_text=matched.group(0), kind=TimeScopeKind.EXPLICIT)
    return TimeScope(raw_text="今年", kind=TimeScopeKind.EXPLICIT) if "今年" in question else None


def _first_present(question: str, terms: tuple[str, ...]) -> str | None:
    return next((term for term in terms if term in question), None)


def _requires_time_clarification(question: str) -> bool:
    """Analysis-window language is not an implicit all-time request."""
    if any(token in question for token in ("总", "累计", "全量", "全部")):
        return False
    return any(token in question for token in ("怎么样", "如何", "为什么", "趋势", "变化", "下降", "增长"))


def _confidence(target: AnalysisTarget) -> float:
    return 0.9 if target.resolution_status is ResolutionStatus.RESOLVED else 0.55


def _ranking(question: str) -> tuple[SortDirection, int] | None:
    """Parse explicit superlatives into a bounded ranking request.

    A ranking is executable only when the same request also names a governed
    breakdown dimension; the caller preserves that distinction rather than
    inventing a dimension from the metric alone.
    """
    if any(token in question for token in ("最高", "最大", "第一", "第1", "top1", "Top1")):
        return SortDirection.DESC, 1
    match = re.search(r"(?:前|top)\s*([1-9]\d{0,1})\s*(?:名|个)?", question, re.IGNORECASE)
    if match:
        return SortDirection.DESC, int(match.group(1))
    if any(token in question for token in ("最低", "最小")):
        return SortDirection.ASC, 1
    return None
