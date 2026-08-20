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

A negative reinvestment rate times a negative ROIC multiplies to a
POSITIVE growth_rate - a real sign trap, not a hypothetical one: found
live on CRCL (Circle Internet Group), where a negative NOPAT and a
negative reinvestment produced growth_rate=+27.7%, which reads as
healthy growth but actually describes a company with a negative return
on capital that is also shrinking its invested base. Flagged with an
explicit warning rather than silently reported as a plain positive
number.

A related but different trap: book stockholders_equity can be crushed
to near-zero (or negative) by years of share buybacks (HD) or
accumulated losses plus leverage (BA), with nothing wrong with the
business itself. Book-value ROIC then explodes to an implausible
100%+ even though reinvestment_rate itself looks ordinary - found live
on HD (roic=144%) and BA (roic=113%). For the most recent fiscal year
only, invested capital is computed with the MARKET value of equity
(market_price x shares_outstanding, passed in by the caller - the same
market_price analyze() already takes for margin_of_safety, not a new
data source) instead of book equity, which sidesteps this distortion:
a heavy-buyback company's market cap doesn't collapse just because its
book equity did. Earlier fiscal years still use book equity, since no
historical price series is available to value them contemporaneously.
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


def _invested_capital(
    statement: FinancialStatement, market_equity: float | None = None
) -> tuple[float | None, list[str]]:
    cash = _value(statement.cash)
    warnings: list[str] = []

    if market_equity is not None:
        equity = market_equity
        warnings.append(
            "invested capital uses market value of equity (market_price x "
            "shares_outstanding), not book equity - avoids the near-zero-book-equity "
            "distortion seen for heavy-buyback/highly-levered firms"
        )
    else:
        equity = _value(statement.stockholders_equity)

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
    statement: FinancialStatement,
    fcff_result: FCFFResult | None,
    market_equity: float | None = None,
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

        invested_capital, ic_warnings = _invested_capital(statement, market_equity)
        warnings.extend(ic_warnings)
        if invested_capital is None:
            warnings.append("stockholders_equity/cash not found - cannot compute ROIC")
        elif invested_capital <= 0:
            warnings.append("invested capital is zero or negative - ROIC is not meaningful")
        else:
            roic = fcff_result.nopat / invested_capital
            growth_rate = reinvestment_rate * roic
            if reinvestment_rate < 0 and roic < 0:
                warnings.append(
                    "reinvestment_rate and ROIC are both negative - growth_rate is a "
                    "misleading positive number (two negatives multiplying), not a "
                    "real growth signal"
                )

    return FundamentalGrowthYear(
        fiscal_year=statement.fiscal_year,
        period_end=statement.period_end,
        reinvestment_rate=reinvestment_rate,
        roic=roic,
        growth_rate=growth_rate,
        warnings=warnings,
    )


def estimate_fundamental_growth_rate(
    statements: list[FinancialStatement], tax_rate: float, market_price: float | None = None
) -> FundamentalGrowthEstimate:
    ordered = sorted(statements, key=lambda s: s.period_end)
    fcff_by_period = {r.period_end: r for r in compute_fcff_series(ordered, tax_rate)}

    by_year = []
    for i, statement in enumerate(ordered):
        market_equity = None
        if market_price is not None and i == len(ordered) - 1:
            shares_outstanding = _value(statement.shares_outstanding)
            if shares_outstanding is not None:
                market_equity = market_price * shares_outstanding
        by_year.append(
            _growth_year(statement, fcff_by_period.get(statement.period_end), market_equity)
        )

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
