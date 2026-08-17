"""FCFF, per Damodaran: EBIT(1-T) + D&A - CapEx - change in non-cash NWC.

This is deliberately not the same number as metrics.simple_fcf (OCF -
CapEx) - see app/financials/metrics.py's module docstring. Operating
income is used as an EBIT proxy (a documented simplification: EBIT can
differ from GAAP operating income when a company has non-operating items
inside it, but we don't have a separate EBIT tag normalized).

Non-cash working capital follows Damodaran's definition, not a naive
current-assets-minus-current-liabilities: cash and short-term debt are
financing items, not operating working capital, and excluding them
matters most for cash-rich companies (AAPL, GOOGL) where including cash
would badly distort the change in NWC.
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import FinancialFact, FinancialStatement
from app.valuation.assumptions import BaseFCFMethod


class FCFFResult(BaseModel):
    fiscal_year: int
    period_end: date
    ebit: float | None = None
    nopat: float | None = None
    depreciation_amortization: float | None = None
    capex: float | None = None
    change_in_nwc: float | None = None
    fcff: float | None = None
    warnings: list[str] = []


def _value(fact: FinancialFact | None) -> float | None:
    return fact.value if fact is not None else None


def compute_operating_nwc(statement: FinancialStatement) -> tuple[float | None, list[str]]:
    current_assets = _value(statement.current_assets)
    current_liabilities = _value(statement.current_liabilities)
    cash = _value(statement.cash)
    warnings: list[str] = []

    if current_assets is None or current_liabilities is None or cash is None:
        return None, warnings

    short_term_debt = _value(statement.short_term_debt)
    if short_term_debt is None:
        warnings.append("short_term_debt not found - assumed 0 for non-cash NWC calc")
        short_term_debt = 0.0

    nwc = (current_assets - cash) - (current_liabilities - short_term_debt)
    return nwc, warnings


def compute_fcff_series(
    statements: list[FinancialStatement], tax_rate: float
) -> list[FCFFResult]:
    ordered = sorted(statements, key=lambda s: s.period_end)
    nwc_by_index: list[tuple[float | None, list[str]]] = [
        compute_operating_nwc(s) for s in ordered
    ]

    results = []
    for i, statement in enumerate(ordered):
        ebit = _value(statement.operating_income)
        d_and_a = _value(statement.depreciation_amortization)
        capex = _value(statement.capex)
        warnings = list(nwc_by_index[i][1])

        change_in_nwc = None
        if i == 0:
            warnings.append("no prior fiscal year available to compute change in NWC")
        else:
            current_nwc, _ = nwc_by_index[i]
            prior_nwc, _ = nwc_by_index[i - 1]
            if current_nwc is not None and prior_nwc is not None:
                change_in_nwc = current_nwc - prior_nwc

        nopat = ebit * (1 - tax_rate) if ebit is not None else None
        fcff = None
        if None not in (nopat, d_and_a, capex, change_in_nwc):
            fcff = nopat + d_and_a - capex - change_in_nwc
        else:
            warnings.append("insufficient inputs to compute FCFF for this fiscal year")

        results.append(
            FCFFResult(
                fiscal_year=statement.fiscal_year,
                period_end=statement.period_end,
                ebit=ebit,
                nopat=nopat,
                depreciation_amortization=d_and_a,
                capex=capex,
                change_in_nwc=change_in_nwc,
                fcff=fcff,
                warnings=warnings,
            )
        )

    return results


def select_base_fcff(
    fcff_series: list[FCFFResult], method: BaseFCFMethod
) -> tuple[float | None, list[str]]:
    ordered = sorted(fcff_series, key=lambda r: r.period_end)
    valid = [r.fcff for r in ordered if r.fcff is not None]

    if not valid:
        return None, ["no fiscal year has a computable FCFF"]

    if method == BaseFCFMethod.LATEST_YEAR:
        return valid[-1], []

    n = 3 if method == BaseFCFMethod.THREE_YEAR_AVG else 5
    recent = valid[-n:]
    warnings = []
    if len(recent) < n:
        warnings.append(f"only {len(recent)} of {n} years available for {method.value} base FCFF")
    return sum(recent) / len(recent), warnings
