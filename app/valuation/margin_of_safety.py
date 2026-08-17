"""Market price + as_of_date -> margin of safety, as a range, not a point.

as_of_date enforces filed_date <= as_of_date across every fact used in a
statement: a valuation "as of" a date must never use financial data that
wasn't public yet on that date (the look-ahead-bias principle from
docs/DATA_SPIKE_NOTES.md, finding #3). V1 doesn't backtest, but the
check costs nothing to enforce now and everything to bolt on later.

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


def _statement_filed_date(statement: FinancialStatement) -> date | None:
    dates = []
    for field_name in _FACT_FIELDS:
        fact: FinancialFact | None = getattr(statement, field_name)
        if fact is not None:
            dates.append(fact.filed_date)
    return max(dates) if dates else None


def _filter_look_ahead(
    statements: list[FinancialStatement], as_of_date: date
) -> tuple[list[FinancialStatement], int]:
    eligible = []
    excluded = 0
    for statement in statements:
        filed_date = _statement_filed_date(statement)
        if filed_date is None or filed_date > as_of_date:
            excluded += 1
            continue
        eligible.append(statement)
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
