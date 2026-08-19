"""Fundamental growth rate = Reinvestment Rate x ROIC, per Damodaran.

This is a REFERENCE estimate only, derived purely from a company's own
historical reinvestment behavior and returns on capital (SEC data, same
as everywhere else in this project - no new external data source). It is
never substituted for ValuationAssumptions.fcff_growth_rate, which stays
a required, explicit, always-user-supplied input (see
app/valuation/assumptions.py and docs/VALUATION_METHOD.md) - this module
only reports what the data implies, alongside whatever growth rate the
caller actually assumed, the same side-by-side philosophy used
everywhere else in this project (qualitative risk vs. quantitative MOS,
cross-model disagreement flags) rather than one number silently
overriding another.

    Reinvestment Rate = (CapEx - D&A + change in non-cash NWC) / NOPAT
    ROIC              = NOPAT / (short_term_debt + long_term_debt
                                  + stockholders_equity - cash)
    Growth rate        = Reinvestment Rate * ROIC

Reuses fcff.py's per-year NOPAT/CapEx/D&A/change-in-NWC (same non-cash
NWC definition, not recomputed here) rather than duplicating that logic.
See https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/growth.htm
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import FinancialFact, FinancialStatement
from app.valuation.fcff import FCFFResult, compute_fcff_series

# How many of the most recent fiscal years to average into
# suggested_growth_rate - mirrors BaseFCFMethod.THREE_YEAR_AVG's window,
# for the same reason: a single year's reinvestment/ROIC can be noisy.
YEARS_AVERAGED = 3


class FundamentalGrowthYear(BaseModel):
    fiscal_year: int
    period_end: date
    reinvestment_rate: float | None = None
    roic: float | None = None
    growth_rate: float | None = None
    warnings: list[str] = []


class FundamentalGrowthEstimate(BaseModel):
    by_year: list[FundamentalGrowthYear]
    suggested_growth_rate: float | None
    years_averaged: int
    warnings: list[str] = []


def _value(fact: FinancialFact | None) -> float | None:
    return fact.value if fact is not None else None


def _invested_capital(statement: FinancialStatement) -> tuple[float | None, list[str]]:
    equity = _value(statement.stockholders_equity)
    cash = _value(statement.cash)
    warnings: list[str] = []
    if equity is None or cash is None:
        return None, warnings

    short_term_debt = _value(statement.short_term_debt)
    long_term_debt = _value(statement.long_term_debt)
    if short_term_debt is None:
        warnings.append("short_term_debt not found - assumed 0 for invested capital")
        short_term_debt = 0.0
    if long_term_debt is None:
        warnings.append("long_term_debt not found - assumed 0 for invested capital")
        long_term_debt = 0.0

    return short_term_debt + long_term_debt + equity - cash, warnings


def _growth_year(
    statement: FinancialStatement, fcff_result: FCFFResult | None
) -> FundamentalGrowthYear:
    warnings: list[str] = []
    reinvestment_rate = None
    roic = None
    growth_rate = None

    inputs_missing = fcff_result is None or None in (
        fcff_result.nopat,
        fcff_result.capex,
        fcff_result.depreciation_amortization,
        fcff_result.change_in_nwc,
    )
    if inputs_missing:
        warnings.append("insufficient FCFF inputs to compute reinvestment rate")
    elif fcff_result.nopat == 0:
        warnings.append("NOPAT is zero - reinvestment rate is undefined")
    else:
        net_capex = fcff_result.capex - fcff_result.depreciation_amortization
        reinvestment_rate = (net_capex + fcff_result.change_in_nwc) / fcff_result.nopat

        invested_capital, ic_warnings = _invested_capital(statement)
        warnings.extend(ic_warnings)
        if invested_capital is None:
            warnings.append("stockholders_equity/cash not found - cannot compute ROIC")
        elif invested_capital <= 0:
            warnings.append("invested capital is zero or negative - ROIC is not meaningful")
        else:
            roic = fcff_result.nopat / invested_capital
            growth_rate = reinvestment_rate * roic

    return FundamentalGrowthYear(
        fiscal_year=statement.fiscal_year,
        period_end=statement.period_end,
        reinvestment_rate=reinvestment_rate,
        roic=roic,
        growth_rate=growth_rate,
        warnings=warnings,
    )


def estimate_fundamental_growth_rate(
    statements: list[FinancialStatement], tax_rate: float
) -> FundamentalGrowthEstimate:
    ordered = sorted(statements, key=lambda s: s.period_end)
    fcff_by_period = {r.period_end: r for r in compute_fcff_series(ordered, tax_rate)}

    by_year = [_growth_year(s, fcff_by_period.get(s.period_end)) for s in ordered]

    valid = [y.growth_rate for y in by_year if y.growth_rate is not None]
    warnings: list[str] = []
    suggested = None
    years_averaged = 0
    if not valid:
        warnings.append("no fiscal year has a computable fundamental growth rate")
    else:
        recent = valid[-YEARS_AVERAGED:]
        years_averaged = len(recent)
        if years_averaged < YEARS_AVERAGED:
            warnings.append(
                f"only {years_averaged} of {YEARS_AVERAGED} years available for "
                "fundamental growth rate average"
            )
        suggested = sum(recent) / years_averaged

    return FundamentalGrowthEstimate(
        by_year=by_year,
        suggested_growth_rate=suggested,
        years_averaged=years_averaged,
        warnings=warnings,
    )
