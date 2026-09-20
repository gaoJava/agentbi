"""Deterministic calendar-scope lowering for executable semantic predicates."""
from __future__ import annotations

import re
from datetime import date

from agentbi.semantic_query_ir import (
    OntologyReference,
    PredicateOperator,
    SemanticPredicate,
    TimeScope,
    TimeScopeKind,
)


class TimeScopeLoweringError(ValueError): pass
def lower_explicit_time_scope(scope: TimeScope | None, *, date_dimension_id: str, version: str) -> tuple[SemanticPredicate, ...]:
    if scope is None: return ()
    if scope.kind is not TimeScopeKind.EXPLICIT: raise TimeScopeLoweringError("relative time scope has no deterministic calendar range")
    month=re.fullmatch(r"(\d{4})年(\d{1,2})月",scope.raw_text); day=re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})",scope.raw_text); year=re.fullmatch(r"(\d{4})年",scope.raw_text)
    if month:
        start=date(int(month.group(1)),int(month.group(2)),1); end=date(start.year+(start.month==12),1 if start.month==12 else start.month+1,1)
    elif day: start=date.fromisoformat(scope.raw_text); end=date.fromordinal(start.toordinal()+1)
    elif year: start=date(int(year.group(1)),1,1); end=date(start.year+1,1,1)
    else: raise TimeScopeLoweringError(f"unsupported explicit calendar scope: {scope.raw_text}")
    ref=OntologyReference(asset_id=date_dimension_id,version=version)
    return (SemanticPredicate(dimension=ref,operator=PredicateOperator.GTE,value=start.isoformat()),SemanticPredicate(dimension=ref,operator=PredicateOperator.LT,value=end.isoformat()))
def previous_year_scope(scope: TimeScope) -> TimeScope:
    match=re.fullmatch(r"(\d{4})(年.*)",scope.raw_text)
    if scope.kind is not TimeScopeKind.EXPLICIT or not match: raise TimeScopeLoweringError("previous-year baseline requires explicit calendar scope")
    return scope.model_copy(update={"raw_text":f"{int(match.group(1))-1}{match.group(2)}"})
