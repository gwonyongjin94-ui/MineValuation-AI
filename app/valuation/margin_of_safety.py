"""Market price + as_of_date -> margin of safety, as a range, not a point.

The as-of-date resolution itself lives in
app/financials/point_in_time.py - it is a fact about what was knowable
on a date, not a property of this calculation, and every other
valuation method needs the same filtering. analyze() applies it once up
front; the call kept here makes this function correct on its own for
callers that reach it directly, and costs nothing because filtering is
idempotent.

margin_of_safety_low/high come from the DCF's own sensitivity grid, not
a separately invented range - MOS = (intrinsic - market) / intrinsic is
monotonic in intrinsic value for intrinsic > 0, so the grid's min/max
value_per_share map directly to the MOS range.
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import FinancialStatement
from app.financials.point_in_time import filter_look_ahead
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import DCFResult, UnsupportedValuationError, run_dcf_valuation


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
    eligible, excluded = filter_look_ahead(statements, as_of_date)
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
