"""Market price + as_of_date -> margin of safety, as a range, not a point.

as_of_date resolves each metric to the most-recently-known fact as of
that date - the as-reported value, or a later restatement, whichever
was actually public by then - not frozen at whatever was originally
reported. Both parts matter and are easy to conflate:

- filed_date <= as_of_date (never use data that wasn't public yet -
  the look-ahead-bias principle from docs/DATA_SPIKE_NOTES.md finding
  #3)
- among everything that WAS public by as_of_date (the as-reported fact
  and any restatements filed before it), use the latest one - a real
  10-K/A restatement is public information the moment it's filed, and
  an "as of" valuation after that date should reflect it, not the
  superseded original figure.

An earlier version of this module only implemented the first half
(whole-statement look-ahead filtering by the as-reported fact's
filed_date) and never consulted restated_facts at all, so an as_of_date
well after a restatement still used the pre-restatement number - caught
via code review, not spike or test.

margin_of_safety_low/high come from the DCF's own sensitivity grid, not
a separately invented range - MOS = (intrinsic - market) / intrinsic is
monotonic in intrinsic value for intrinsic > 0, so the grid's min/max
value_per_share map directly to the MOS range.
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import FinancialFact, FinancialStatement
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import DCFResult, UnsupportedValuationError, run_dcf_valuation

_FACT_FIELDS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "depreciation_amortization",
    "capex",
    "current_assets",
    "current_liabilities",
    "cash",
    "short_term_debt",
    "long_term_debt",
    "stockholders_equity",
    "shares_outstanding",
)


class MarginOfSafetyResult(BaseModel):
    market_price: float
    as_of_date: date
    intrinsic_value_per_share: float | None
    margin_of_safety: float | None
    intrinsic_value_low: float | None
    intrinsic_value_high: float | None
    margin_of_safety_low: float | None
    margin_of_safety_high: float | None
    statements_used: int
    statements_excluded_look_ahead: int
    dcf: DCFResult
    warnings: list[str] = []


def _resolve_fact_as_of(
    as_reported: FinancialFact | None,
    restatements: list[FinancialFact],
    as_of_date: date,
) -> FinancialFact | None:
    candidates = list(restatements)
    if as_reported is not None:
        candidates.append(as_reported)
    known = [fact for fact in candidates if fact.filed_date <= as_of_date]
    if not known:
        return None
    return max(known, key=lambda fact: fact.filed_date)


def _resolve_statement_as_of(
    statement: FinancialStatement, as_of_date: date
) -> FinancialStatement | None:
    restated_by_metric: dict[str, list[FinancialFact]] = {}
    for fact in statement.restated_facts:
        restated_by_metric.setdefault(fact.metric, []).append(fact)

    resolved = {
        field_name: _resolve_fact_as_of(
            getattr(statement, field_name), restated_by_metric.get(field_name, []), as_of_date
        )
        for field_name in _FACT_FIELDS
    }

    if all(fact is None for fact in resolved.values()):
        return None

    return statement.model_copy(update=resolved)


def _filter_look_ahead(
    statements: list[FinancialStatement], as_of_date: date
) -> tuple[list[FinancialStatement], int]:
    eligible = []
    excluded = 0
    for statement in statements:
        resolved = _resolve_statement_as_of(statement, as_of_date)
        if resolved is None:
            excluded += 1
            continue
        eligible.append(resolved)
    return eligible, excluded


def _margin_of_safety(intrinsic_value: float | None, market_price: float) -> float | None:
    if intrinsic_value is None or intrinsic_value <= 0:
        return None
    return (intrinsic_value - market_price) / intrinsic_value


def compute_margin_of_safety(
    statements: list[FinancialStatement],
    assumptions: ValuationAssumptions,
    market_price: float,
    as_of_date: date,
) -> MarginOfSafetyResult:
    eligible, excluded = _filter_look_ahead(statements, as_of_date)
    if not eligible:
        raise UnsupportedValuationError(
            f"no financial statements filed on or before {as_of_date.isoformat()}"
        )

    dcf = run_dcf_valuation(eligible, assumptions)

    warnings = list(dcf.warnings)
    if excluded:
        warnings.append(
            f"excluded {excluded} financial statement(s) filed after "
            f"{as_of_date.isoformat()} to avoid look-ahead bias"
        )

    valid_sensitivity = [
        p.value_per_share for p in dcf.sensitivity if p.value_per_share is not None
    ]
    iv_low = min(valid_sensitivity) if valid_sensitivity else None
    iv_high = max(valid_sensitivity) if valid_sensitivity else None

    return MarginOfSafetyResult(
        market_price=market_price,
        as_of_date=as_of_date,
        intrinsic_value_per_share=dcf.value_per_share,
        margin_of_safety=_margin_of_safety(dcf.value_per_share, market_price),
        intrinsic_value_low=iv_low,
        intrinsic_value_high=iv_high,
        margin_of_safety_low=_margin_of_safety(iv_low, market_price),
        margin_of_safety_high=_margin_of_safety(iv_high, market_price),
        statements_used=len(eligible),
        statements_excluded_look_ahead=excluded,
        dcf=dcf,
        warnings=warnings,
    )
