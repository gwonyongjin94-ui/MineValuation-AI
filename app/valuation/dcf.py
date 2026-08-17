"""FCFF -> enterprise value -> equity value -> value per share.

Never returns a single point estimate without its sensitivity: a DCF's
output is only as good as its assumptions, and margin-of-safety
reasoning needs a range, not a falsely precise number. run_dcf_valuation
is the entry point Phase 6/7 should call.
"""

from pydantic import BaseModel

from app.data.models import FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.fcff import compute_fcff_series, select_base_fcff

TERMINAL_VALUE_WARNING_THRESHOLD = 0.75
DEFAULT_SENSITIVITY_DELTAS = (-0.01, 0.0, 0.01)


class UnsupportedValuationError(Exception):
    pass


class SensitivityPoint(BaseModel):
    discount_rate: float
    terminal_growth_rate: float
    value_per_share: float | None


class DCFResult(BaseModel):
    base_fcff: float
    projected_fcff: list[float]
    discounted_fcff: list[float]
    terminal_value: float
    discounted_terminal_value: float
    enterprise_value: float
    terminal_value_pct_of_ev: float | None
    cash: float | None = None
    total_debt: float | None = None
    equity_value: float | None = None
    shares_outstanding: float | None = None
    value_per_share: float | None = None
    assumptions: ValuationAssumptions
    sensitivity: list[SensitivityPoint] = []
    warnings: list[str] = []


def _value(fact: FinancialFact | None) -> float | None:
    return fact.value if fact is not None else None


def project_fcff(base_fcff: float, growth_rate: float, years: int) -> list[float]:
    return [base_fcff * (1 + growth_rate) ** t for t in range(1, years + 1)]


def discount_cash_flows(cash_flows: list[float], discount_rate: float) -> list[float]:
    return [cf / (1 + discount_rate) ** t for t, cf in enumerate(cash_flows, start=1)]


def compute_terminal_value(
    final_year_fcff: float, discount_rate: float, terminal_growth_rate: float
) -> float:
    return final_year_fcff * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate)


def run_dcf(
    statement: FinancialStatement, base_fcff: float, assumptions: ValuationAssumptions
) -> DCFResult:
    projected = project_fcff(base_fcff, assumptions.fcff_growth_rate, assumptions.forecast_years)
    discounted = discount_cash_flows(projected, assumptions.discount_rate)

    tv = compute_terminal_value(
        projected[-1], assumptions.discount_rate, assumptions.terminal_growth_rate
    )
    discounted_tv = tv / (1 + assumptions.discount_rate) ** assumptions.forecast_years
    enterprise_value = sum(discounted) + discounted_tv
    tv_pct = discounted_tv / enterprise_value if enterprise_value else None

    warnings: list[str] = []
    if tv_pct is not None and tv_pct > TERMINAL_VALUE_WARNING_THRESHOLD:
        warnings.append(
            f"terminal value is {tv_pct:.0%} of enterprise value - low-confidence estimate"
        )

    cash = _value(statement.cash)
    short_term_debt = _value(statement.short_term_debt) or 0.0
    long_term_debt = _value(statement.long_term_debt) or 0.0
    total_debt = short_term_debt + long_term_debt
    shares_outstanding = _value(statement.shares_outstanding)

    equity_value = None
    value_per_share = None
    if cash is None:
        warnings.append("cash not found - cannot bridge enterprise value to equity value")
    else:
        equity_value = enterprise_value + cash - total_debt
        if not shares_outstanding:
            warnings.append("shares_outstanding not found - cannot compute value per share")
        else:
            value_per_share = equity_value / shares_outstanding

    return DCFResult(
        base_fcff=base_fcff,
        projected_fcff=projected,
        discounted_fcff=discounted,
        terminal_value=tv,
        discounted_terminal_value=discounted_tv,
        enterprise_value=enterprise_value,
        terminal_value_pct_of_ev=tv_pct,
        cash=cash,
        total_debt=total_debt,
        equity_value=equity_value,
        shares_outstanding=shares_outstanding,
        value_per_share=value_per_share,
        assumptions=assumptions,
        warnings=warnings,
    )


def run_sensitivity(
    statement: FinancialStatement,
    base_fcff: float,
    assumptions: ValuationAssumptions,
    discount_rate_deltas: tuple[float, ...] = DEFAULT_SENSITIVITY_DELTAS,
    terminal_growth_deltas: tuple[float, ...] = DEFAULT_SENSITIVITY_DELTAS,
) -> list[SensitivityPoint]:
    points = []
    for dr_delta in discount_rate_deltas:
        for tg_delta in terminal_growth_deltas:
            discount_rate = assumptions.discount_rate + dr_delta
            terminal_growth_rate = assumptions.terminal_growth_rate + tg_delta
            if terminal_growth_rate >= discount_rate:
                points.append(
                    SensitivityPoint(
                        discount_rate=discount_rate,
                        terminal_growth_rate=terminal_growth_rate,
                        value_per_share=None,
                    )
                )
                continue
            varied = assumptions.model_copy(
                update={
                    "discount_rate": discount_rate,
                    "terminal_growth_rate": terminal_growth_rate,
                }
            )
            result = run_dcf(statement, base_fcff, varied)
            points.append(
                SensitivityPoint(
                    discount_rate=discount_rate,
                    terminal_growth_rate=terminal_growth_rate,
                    value_per_share=result.value_per_share,
                )
            )
    return points


def run_dcf_valuation(
    statements: list[FinancialStatement], assumptions: ValuationAssumptions
) -> DCFResult:
    if not statements:
        raise UnsupportedValuationError("no financial statements available")

    latest = max(statements, key=lambda s: s.period_end)
    if latest.company.valuation_category != ValuationCategory.STANDARD:
        raise UnsupportedValuationError(
            "FCFF/DCF is not supported for valuation_category="
            f"{latest.company.valuation_category.value}"
        )

    fcff_series = compute_fcff_series(statements, assumptions.tax_rate)
    base_fcff, base_warnings = select_base_fcff(fcff_series, assumptions.base_fcf_method)
    if base_fcff is None:
        raise UnsupportedValuationError("insufficient data to compute a base FCFF")

    result = run_dcf(latest, base_fcff, assumptions)
    result.warnings = base_warnings + result.warnings
    result.sensitivity = run_sensitivity(latest, base_fcff, assumptions)
    return result
