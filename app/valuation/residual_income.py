"""Residual Income Model (Ohlson, 1995, "Earnings, Book Values, and
Dividends in Equity Valuation", Contemporary Accounting Research 11(2),
661-687) / EVA (Stewart, 1991, "The Quest for Value"): starts from book
value - already on the balance sheet, not forecast - and adds only the
present value of future *excess* earnings, net income above what the
cost of equity capital alone would require.

    RI(t) = NetIncome(t) - cost_of_equity * BookValue(t-1)
    Value = BookValue(0) + sum(PV(RI(t))) + PV(terminal RI)

Institutional research uses this specifically because it sidesteps
DCF's terminal-value dominance (see the "DCF-시장가 괴리" research memo -
terminal value is typically 60-80% of a standard FCFF DCF's output): if
a company only ever earns exactly its cost of equity, residual income
is zero and the model collapses to "value = book value" regardless of
the forecast horizon, so the explicit forecast years do far more of the
real work here than in FCFF DCF.

Two documented simplifications, both flagged via warnings on the
result rather than silently assumed:

- Book value compounds via full earnings retention (BookValue(t) =
  BookValue(t-1) + NetIncome(t)). The textbook clean-surplus relation
  is BookValue(t) = BookValue(t-1) + NetIncome(t) - Dividends(t), but
  this project doesn't normalize a dividends-paid tag (see
  DATA_MODEL.md), so retention is assumed 100%. Overstates book value
  (and correspondingly understates the residual-income effect) for a
  real dividend payer - a bigger distortion the more of net income a
  company actually pays out.
- Net income is projected using the SAME fcff_growth_rate assumption
  the FCFF DCF uses, for cross-method comparability inside
  valuation_consensus - not because it's independently the most
  defensible growth path for net income specifically.

cost_of_equity is a required parameter here, not computed in this
module - the caller (app/services/analysis_service.py) supplies
wacc_estimate's CAPM-derived cost_of_equity when available, falling
back to assumptions.discount_rate (with a warning) otherwise. This
module stays pure and network-free, matching normalizer.py's and
dcf.py's own design.
"""

from pydantic import BaseModel

from app.data.models import FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import (
    DEFAULT_SENSITIVITY_DELTAS,
    SensitivityPoint,
    UnsupportedValuationError,
    discount_cash_flows,
)

TERMINAL_RI_WARNING_THRESHOLD = 0.75


def _value(fact: FinancialFact | None) -> float | None:
    return fact.value if fact is not None else None


def project_residual_income(
    book_value: float,
    net_income_base: float,
    growth_rate: float,
    cost_of_equity: float,
    years: int,
) -> tuple[list[float], list[float], list[float]]:
    projected_net_income: list[float] = []
    projected_book_value: list[float] = []
    projected_residual_income: list[float] = []
    bv = book_value
    for t in range(1, years + 1):
        ni = net_income_base * (1 + growth_rate) ** t
        ri = ni - cost_of_equity * bv
        bv = bv + ni  # full-retention clean-surplus assumption - see module docstring
        projected_net_income.append(ni)
        projected_book_value.append(bv)
        projected_residual_income.append(ri)
    return projected_net_income, projected_book_value, projected_residual_income


class ResidualIncomeResult(BaseModel):
    book_value: float
    net_income_base: float
    cost_of_equity: float
    projected_net_income: list[float] = []
    projected_book_value: list[float] = []
    projected_residual_income: list[float] = []
    discounted_residual_income: list[float] = []
    terminal_residual_income: float
    discounted_terminal_residual_income: float
    equity_value: float
    terminal_pct_of_equity_value: float | None
    shares_outstanding: float | None = None
    value_per_share: float | None = None
    assumptions: ValuationAssumptions
    sensitivity: list[SensitivityPoint] = []
    warnings: list[str] = []


def run_residual_income(
    statement: FinancialStatement,
    book_value: float,
    net_income_base: float,
    cost_of_equity: float,
    assumptions: ValuationAssumptions,
) -> ResidualIncomeResult:
    if cost_of_equity <= assumptions.terminal_growth_rate:
        # Same guard ValuationAssumptions itself applies to
        # discount_rate vs terminal_growth_rate - the terminal RI
        # perpetuity is undefined/negative otherwise.
        raise UnsupportedValuationError(
            "cost_of_equity must exceed terminal_growth_rate for a residual income terminal value"
        )

    projected_ni, projected_bv, projected_ri = project_residual_income(
        book_value,
        net_income_base,
        assumptions.fcff_growth_rate,
        cost_of_equity,
        assumptions.forecast_years,
    )
    discounted_ri = discount_cash_flows(projected_ri, cost_of_equity)

    final_ri = projected_ri[-1]
    terminal_ri = (
        final_ri
        * (1 + assumptions.terminal_growth_rate)
        / (cost_of_equity - assumptions.terminal_growth_rate)
    )
    discounted_terminal_ri = terminal_ri / (1 + cost_of_equity) ** assumptions.forecast_years

    equity_value = book_value + sum(discounted_ri) + discounted_terminal_ri
    terminal_pct = discounted_terminal_ri / equity_value if equity_value else None

    warnings: list[str] = []
    if terminal_pct is not None and terminal_pct > TERMINAL_RI_WARNING_THRESHOLD:
        warnings.append(
            f"terminal residual income is {terminal_pct:.0%} of equity value - "
            "low-confidence estimate"
        )

    shares_outstanding = _value(statement.shares_outstanding)
    value_per_share = None
    if not shares_outstanding:
        warnings.append("shares_outstanding not found - cannot compute value per share")
    else:
        value_per_share = equity_value / shares_outstanding

    return ResidualIncomeResult(
        book_value=book_value,
        net_income_base=net_income_base,
        cost_of_equity=cost_of_equity,
        projected_net_income=projected_ni,
        projected_book_value=projected_bv,
        projected_residual_income=projected_ri,
        discounted_residual_income=discounted_ri,
        terminal_residual_income=terminal_ri,
        discounted_terminal_residual_income=discounted_terminal_ri,
        equity_value=equity_value,
        terminal_pct_of_equity_value=terminal_pct,
        shares_outstanding=shares_outstanding,
        value_per_share=value_per_share,
        assumptions=assumptions,
        warnings=warnings,
    )


def run_residual_income_sensitivity(
    statement: FinancialStatement,
    book_value: float,
    net_income_base: float,
    cost_of_equity: float,
    assumptions: ValuationAssumptions,
    cost_of_equity_deltas: tuple[float, ...] = DEFAULT_SENSITIVITY_DELTAS,
    terminal_growth_deltas: tuple[float, ...] = DEFAULT_SENSITIVITY_DELTAS,
) -> list[SensitivityPoint]:
    """Same grid-search shape as dcf.run_sensitivity_with(), varying
    cost_of_equity (RIM's discount rate - reported in
    SensitivityPoint.discount_rate, the same field DCF's grid uses for
    its own discount rate) and terminal_growth_rate instead of
    assumptions.discount_rate, since cost_of_equity is a parameter to
    this module rather than part of ValuationAssumptions.
    """
    points = []
    for coe_delta in cost_of_equity_deltas:
        for tg_delta in terminal_growth_deltas:
            varied_coe = cost_of_equity + coe_delta
            varied_tg = assumptions.terminal_growth_rate + tg_delta
            if varied_tg >= varied_coe:
                points.append(
                    SensitivityPoint(
                        discount_rate=varied_coe,
                        terminal_growth_rate=varied_tg,
                        value_per_share=None,
                    )
                )
                continue
            varied_assumptions = assumptions.model_copy(
                update={"terminal_growth_rate": varied_tg}
            )
            result = run_residual_income(
                statement, book_value, net_income_base, varied_coe, varied_assumptions
            )
            points.append(
                SensitivityPoint(
                    discount_rate=varied_coe,
                    terminal_growth_rate=varied_tg,
                    value_per_share=result.value_per_share,
                )
            )
    return points


def run_residual_income_valuation(
    statements: list[FinancialStatement],
    assumptions: ValuationAssumptions,
    cost_of_equity: float,
) -> ResidualIncomeResult:
    if not statements:
        raise UnsupportedValuationError("no financial statements available")

    latest = max(statements, key=lambda s: s.period_end)
    if latest.company.valuation_category != ValuationCategory.STANDARD:
        raise UnsupportedValuationError(
            "Residual Income valuation is not supported for valuation_category="
            f"{latest.company.valuation_category.value}"
        )

    book_value = _value(latest.stockholders_equity)
    net_income_base = _value(latest.net_income)
    if book_value is None or net_income_base is None:
        raise UnsupportedValuationError(
            "insufficient data (stockholders_equity/net_income) for residual income"
        )
    if book_value <= 0:
        raise UnsupportedValuationError(
            "non-positive book value - residual income model not meaningful"
        )

    result = run_residual_income(latest, book_value, net_income_base, cost_of_equity, assumptions)
    result.sensitivity = run_residual_income_sensitivity(
        latest, book_value, net_income_base, cost_of_equity, assumptions
    )
    return result


class ResidualIncomeEstimate(BaseModel):
    result: ResidualIncomeResult
    value_per_share_low: float | None = None
    value_per_share_high: float | None = None
    warnings: list[str] = []


def run_residual_income_estimate(
    statements: list[FinancialStatement],
    assumptions: ValuationAssumptions,
    cost_of_equity: float,
) -> ResidualIncomeEstimate:
    result = run_residual_income_valuation(statements, assumptions, cost_of_equity)
    valid = [p.value_per_share for p in result.sensitivity if p.value_per_share is not None]
    return ResidualIncomeEstimate(
        result=result,
        value_per_share_low=min(valid) if valid else None,
        value_per_share_high=max(valid) if valid else None,
        warnings=result.warnings,
    )
