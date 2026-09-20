"""Evidence packaging, grounded synthesis validation, and deterministic fallback."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .agent_loop import StopReason
from .observation import (
    AnalysisState,
    ObservationComparison,
    ObservationDirection,
    ObservationStatus,
)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_ids: tuple[str, ...]
    comparison_id: str


class MetricEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric_asset_id: str
    current: Decimal | None
    baseline: Decimal | None
    delta: Decimal | None
    delta_pct: Decimal | None
    direction: ObservationDirection
    evidence: EvidenceReference


class ContributionEvidence(MetricEvidence):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member: tuple[str, ...]
    share_of_total_decline: Decimal | None = None


class BreakdownEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dimension_asset_id: str
    ranking_basis: str = "DELTA"
    negative_contributors: tuple[ContributionEvidence, ...] = ()
    positive_offsets: tuple[ContributionEvidence, ...] = ()


class UnavailableEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: str
    metric_asset_id: str
    dimensions: tuple[str, ...]
    status: ObservationStatus
    reason: str | None = None


class EvidencePackage(BaseModel):
    """The synthesizer's sole fact source; no raw execution objects or secrets."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    package_id: str = Field(default_factory=lambda: str(uuid4()))
    analysis_plan_id: str | None = None
    target_metric_id: str
    scope_filters: tuple[dict, ...] = ()
    current_period_filters: tuple[dict, ...] = ()
    baseline_period_filters: tuple[dict, ...] = ()
    primary_metric: MetricEvidence
    breakdowns: tuple[BreakdownEvidence, ...] = ()
    driver_movements: tuple[MetricEvidence, ...] = ()
    unavailable_evidence: tuple[UnavailableEvidence, ...] = ()
    stop_reason: StopReason | None = None
    completeness: str


class EvidencePackageBuilder:
    def __init__(self, *, top_n: int = 3): self._top_n = top_n

    def build(self, state: AnalysisState, *, target_metric_id: str, stop_reason: StopReason | None) -> EvidencePackage:
        primary = next(item for item in state.comparisons if item.metric_asset_id == target_metric_id and not item.dimensions)
        primary_fact = self._metric(primary, state)
        breakdowns = tuple(self._breakdown(item, state, primary_fact) for item in state.comparisons if item.metric_asset_id == target_metric_id and item.dimensions)
        drivers = tuple(self._metric(item, state) for item in state.comparisons if item.metric_asset_id != target_metric_id and not item.dimensions)
        current, baseline = self._pair_observations(state, primary)
        unavailable = tuple(UnavailableEvidence(observation_id=item.observation_id, metric_asset_id=item.metric_asset_id,
                                                dimensions=item.dimensions, status=item.status,
                                                reason=item.provenance.error_message)
                            for item in state.observations if item.status is not ObservationStatus.SUCCESS)
        return EvidencePackage(analysis_plan_id=current.provenance.analysis_plan_id, target_metric_id=target_metric_id,
                               scope_filters=self._common_filters(current.filters, baseline.filters),
                               current_period_filters=current.filters, baseline_period_filters=baseline.filters,
                               primary_metric=primary_fact, breakdowns=breakdowns, driver_movements=drivers,
                               unavailable_evidence=unavailable, stop_reason=stop_reason,
                               completeness="FIRST_LEVEL_EVIDENCE_COMPLETE" if stop_reason is StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE else "PARTIAL")

    def _breakdown(self, comparison: ObservationComparison, state: AnalysisState, primary: MetricEvidence) -> BreakdownEvidence:
        dimension = comparison.dimensions[0]
        entries = [self._metric_member(comparison, item, state, primary) for item in comparison.members]
        negative = tuple(sorted((item for item in entries if item.delta is not None and item.delta < 0), key=lambda item: item.delta)[:self._top_n])
        positive = tuple(sorted((item for item in entries if item.delta is not None and item.delta > 0), key=lambda item: item.delta, reverse=True)[:self._top_n])
        return BreakdownEvidence(dimension_asset_id=dimension, negative_contributors=negative, positive_offsets=positive)

    @staticmethod
    def _common_filters(current, baseline):
        base = {str(item) for item in baseline}
        return tuple(item for item in current if str(item) in base)

    def _metric(self, comparison: ObservationComparison, state: AnalysisState) -> MetricEvidence:
        member = comparison.members[0] if comparison.members else None
        return MetricEvidence(metric_asset_id=comparison.metric_asset_id,
                              current=member.current if member else None, baseline=member.baseline if member else None,
                              delta=member.delta if member else None, delta_pct=member.delta_pct if member else None,
                              direction=member.direction if member else ObservationDirection.UNDEFINED,
                              evidence=self._reference(comparison))

    def _metric_member(self, comparison, member, state, primary) -> ContributionEvidence:
        decline_share = None
        if primary.delta is not None and primary.delta < 0 and member.delta is not None and member.delta < 0:
            decline_share = abs(member.delta) / abs(primary.delta)
        return ContributionEvidence(metric_asset_id=comparison.metric_asset_id, member=member.member,
                                    current=member.current, baseline=member.baseline, delta=member.delta,
                                    delta_pct=member.delta_pct, direction=member.direction,
                                    share_of_total_decline=decline_share, evidence=self._reference(comparison))

    @staticmethod
    def _reference(comparison):
        return EvidenceReference(observation_ids=(comparison.current_observation_id, comparison.baseline_observation_id), comparison_id=comparison.comparison_id)

    @staticmethod
    def _pair_observations(state, comparison):
        items = {item.observation_id: item for item in state.observations}
        return items[comparison.current_observation_id], items[comparison.baseline_observation_id]


class SynthesisFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    evidence_refs: tuple[str, ...]
    mentioned_members: tuple[str, ...] = ()
    numeric_values: tuple[str, ...] = ()


class FinalAnalysisAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str
    key_findings: tuple[SynthesisFinding, ...]
    breakdown_findings: tuple[SynthesisFinding, ...]
    driver_findings: tuple[SynthesisFinding, ...]
    limitations: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    scope_filters: tuple[dict, ...]
    current_period_filters: tuple[dict, ...]
    baseline_period_filters: tuple[dict, ...]
    synthesis_mode: str


class EvidenceNarrator(Protocol):
    async def synthesize(self, package: EvidencePackage) -> FinalAnalysisAnswer: ...


class GroundingValidator:
    """Reject output whose refs, members, numeric facts, scope, or claims drift."""

    def validate(self, answer: FinalAnalysisAnswer, package: EvidencePackage) -> tuple[bool, tuple[str, ...]]:
        allowed_refs = self._refs(package); allowed_members = self._members(package); allowed_numbers = self._numbers(package)
        errors = []
        refs = set(answer.evidence_refs) | {ref for finding in self._findings(answer) for ref in finding.evidence_refs}
        if not refs or not refs <= allowed_refs: errors.append("unknown evidence reference")
        mentioned = {member for finding in self._findings(answer) for member in finding.mentioned_members}
        if not mentioned <= allowed_members: errors.append("unknown contribution member")
        declared_numbers = {value for finding in self._findings(answer) for value in finding.numeric_values}
        if not declared_numbers <= allowed_numbers: errors.append("unknown numeric fact")
        if "channel" in self._text(answer).casefold() or "渠道" in self._text(answer): errors.append("unsupported dimension mentioned")
        if answer.scope_filters != package.scope_filters: errors.append("scope drift")
        if answer.current_period_filters != package.current_period_filters or answer.baseline_period_filters != package.baseline_period_filters: errors.append("period drift")
        text = self._text(answer).casefold()
        if any(marker in text for marker in ("因果影响", "因果贡献", "因果证明", "causal impact", "causal contribution", "causal proof")):
            errors.append("causal overclaim")
        return not errors, tuple(errors)

    @staticmethod
    def _findings(answer): return (*answer.key_findings, *answer.breakdown_findings, *answer.driver_findings)
    def _refs(self, package):
        items = [package.primary_metric, *package.driver_movements]
        for breakdown in package.breakdowns: items.extend((*breakdown.negative_contributors, *breakdown.positive_offsets))
        return {ref for item in items for ref in (*item.evidence.observation_ids, item.evidence.comparison_id)}
    def _members(self, package):
        return {member for breakdown in package.breakdowns for item in (*breakdown.negative_contributors, *breakdown.positive_offsets) for member in item.member}
    def _numbers(self, package):
        values = []
        for item in [package.primary_metric, *package.driver_movements]: values.extend((item.current, item.baseline, item.delta, item.delta_pct))
        for breakdown in package.breakdowns:
            for item in (*breakdown.negative_contributors, *breakdown.positive_offsets): values.extend((item.current, item.baseline, item.delta, item.delta_pct, item.share_of_total_decline))
        return {str(value) for value in values if value is not None}
    def _text(self, answer): return " ".join([answer.summary, *(item.text for item in self._findings(answer)), *answer.limitations])


class DeterministicFallbackSynthesizer:
    """Grounded answer that performs no arithmetic or ranking after packaging."""

    def synthesize(self, package: EvidencePackage) -> FinalAnalysisAnswer:
        primary = package.primary_metric; refs = self._refs(package)
        summary = f"{primary.metric_asset_id} 从 {primary.baseline} 变为 {primary.current}，变化 {primary.delta}（{primary.delta_pct}），方向为 {primary.direction.value}。"
        breakdown = tuple(self._breakdown(item) for item in package.breakdowns)
        drivers = tuple(self._driver(item) for item in package.driver_movements)
        limitations = ["产品、客户与 driver movement 是变化证据，不是严格证明。"]
        limitations.extend(f"{item.metric_asset_id} / {','.join(item.dimensions) or 'scalar'}：{item.status.value}" for item in package.unavailable_evidence)
        return FinalAnalysisAnswer(summary=summary,
                                   key_findings=(SynthesisFinding(text=summary, evidence_refs=refs, numeric_values=self._numbers(primary)),),
                                   breakdown_findings=breakdown, driver_findings=drivers, limitations=tuple(limitations),
                                   evidence_refs=refs, scope_filters=package.scope_filters,
                                   current_period_filters=package.current_period_filters,
                                   baseline_period_filters=package.baseline_period_filters,
                                   synthesis_mode="DETERMINISTIC_FALLBACK")

    def _breakdown(self, item):
        negatives = "; ".join(self._contribution(value) for value in item.negative_contributors) or "无负向成员"
        positives = "; ".join(self._contribution(value) for value in item.positive_offsets) or "无正向抵消成员"
        values = (*item.negative_contributors, *item.positive_offsets)
        return SynthesisFinding(text=f"{item.dimension_asset_id} 按 DELTA 排名：负向 {negatives}；正向抵消 {positives}。",
                                evidence_refs=tuple(ref for value in values for ref in (*value.evidence.observation_ids, value.evidence.comparison_id)),
                                mentioned_members=tuple(member for value in values for member in value.member),
                                numeric_values=tuple(number for value in values for number in self._numbers(value)))

    def _driver(self, item):
        text = f"{item.metric_asset_id}：{item.baseline} → {item.current}，变化 {item.delta}（{item.delta_pct}），{item.direction.value}。"
        return SynthesisFinding(text=text, evidence_refs=(*item.evidence.observation_ids, item.evidence.comparison_id), numeric_values=self._numbers(item))

    @staticmethod
    def _contribution(item): return f"{' / '.join(item.member)}：{item.delta}"
    @staticmethod
    def _numbers(item): return tuple(str(value) for value in (item.current, item.baseline, item.delta, item.delta_pct, getattr(item, 'share_of_total_decline', None)) if value is not None)
    @staticmethod
    def _refs(package):
        return tuple(ref for item in (package.primary_metric, *package.driver_movements) for ref in (*item.evidence.observation_ids, item.evidence.comparison_id))


class EvidenceGroundedSynthesizer:
    def __init__(self, narrator: EvidenceNarrator | None = None): self._narrator, self._validator, self._fallback = narrator, GroundingValidator(), DeterministicFallbackSynthesizer()
    async def synthesize(self, package: EvidencePackage) -> tuple[FinalAnalysisAnswer, tuple[str, ...]]:
        if self._narrator is None: return self._fallback.synthesize(package), ()
        try: answer = await self._narrator.synthesize(package)
        except Exception:  # noqa: BLE001 - optional narrator failure uses deterministic fallback.
            return self._fallback.synthesize(package), ("LLM_UNAVAILABLE",)
        valid, errors = self._validator.validate(answer, package)
        return (answer, ()) if valid else (self._fallback.synthesize(package), errors)
