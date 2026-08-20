"""Owner Earnings, per Buffett's 1986 Berkshire Hathaway shareholder
letter: (a) reported net income, plus (b) D&A and other non-cash
charges, minus (c) the capex "a business requires to fully maintain its
long-term competitive position and unit volume", minus (d) any
incremental working capital needed to maintain that volume.

    Owner Earnings = net_income + D&A - maintenance_capex - change_in_NWC

Two real differences from FCFF (fcff.py), not just a rename:
- Starts from **net income** (after interest and tax - an equity-level
  measure of what the owner actually earns) rather than EBIT*(1-tax)
  (NOPAT - a firm-level, pre-interest measure). FCFF values the whole
  firm; Owner Earnings values the equity directly, closer to how
  Buffett himself describes thinking about a business as its owner
  would.
- Splits capex into a maintenance component, not just subtracting the
  total. Buffett's own letter admits maintenance capex "cannot be
  precisely calculated" from GAAP data and "must be a guess" - and SEC
  filings have no tag distinguishing maintenance from growth capex. Per
  this project's rule against inventing a single unverifiable number,
  maintenance capex is bounded instead of guessed at:

    owner_earnings (full capex as maintenance) = assumes ALL capex is
      maintenance (zero growth capex) - the more conservative bound in
      the typical case (capex >= D&A)
    owner_earnings (D&A as maintenance)        = assumes only capex up
      to D&A is maintenance, the rest is growth - a common analyst
      heuristic, and the more optimistic bound in the typical case

  These are labeled by their assumption, not hardcoded "low"/"high" -
  `owner_earnings_low`/`_high` take min/max of the two so the range is
  always correctly ordered even for the atypical case (a shrinking
  business with capex < D&A) where the ordering would otherwise flip.

Change in non-cash NWC reuses fcff.py's compute_operating_nwc() rather
than recomputing it - same definition, same reasoning (cash and
short-term debt are financing items, not operating working capital).
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.dcf import DCFResult, UnsupportedValuationError, run_dcf, run_sensitivity
from app.valuation.fcff import compute_operating_nwc


class OwnerEarningsResult(BaseModel):
    fiscal_year: int
    period_end: date
    net_income: float | None = None
    depreciation_amortization: float | None = None
    capex: float | None = None
    change_in_nwc: float | None = None
    owner_earnings_full_capex_as_maintenance: float | None = None
    owner_earnings_da_as_maintenance: float | None = None
    owner_earnings_low: float | None = None
    owner_earnings_high: float | None = None
    warnings: list[str] = []


def _value(fact: FinancialFact | None) -> float | None:
    return fact.value if fact is not None else None


def compute_owner_earnings_series(
    statements: list[FinancialStatement],
) -> list[OwnerEarningsResult]:
    ordered = sorted(statements, key=lambda s: s.period_end)
    nwc_by_index: list[tuple[float | None, list[str]]] = [compute_operating_nwc(s) for s in ordered]

    results = []
    for i, statement in enumerate(ordered):
        net_income = _value(statement.net_income)
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

        full_capex_variant = None
        da_variant = None
        low = None
        high = None
        if None in (net_income, d_and_a, capex, change_in_nwc):
            warnings.append("insufficient inputs to compute owner earnings for this fiscal year")
        else:
            full_capex_variant = net_income + d_and_a - capex - change_in_nwc
            da_variant = net_income - change_in_nwc  # d_and_a cancels: + d_and_a - d_and_a
            low = min(full_capex_variant, da_variant)
            high = max(full_capex_variant, da_variant)

        results.append(
            OwnerEarningsResult(
                fiscal_year=statement.fiscal_year,
                period_end=statement.period_end,
                net_income=net_income,
                depreciation_amortization=d_and_a,
                capex=capex,
                change_in_nwc=change_in_nwc,
                owner_earnings_full_capex_as_maintenance=full_capex_variant,
                owner_earnings_da_as_maintenance=da_variant,
                owner_earnings_low=low,
                owner_earnings_high=high,
                warnings=warnings,
            )
        )

    return results


def select_base_owner_earnings(
    series: list[OwnerEarningsResult], method: BaseFCFMethod
) -> tuple[float | None, float | None, list[str]]:
    """Same averaging-window logic as fcff.select_base_fcff(), applied to
    the low and high owner-earnings series independently."""
    ordered = sorted(series, key=lambda r: r.period_end)
    valid_low = [r.owner_earnings_low for r in ordered if r.owner_earnings_low is not None]
    valid_high = [r.owner_earnings_high for r in ordered if r.owner_earnings_high is not None]

    if not valid_low or not valid_high:
        return None, None, ["no fiscal year has computable owner earnings"]

    if method == BaseFCFMethod.LATEST_YEAR:
        return valid_low[-1], valid_high[-1], []

    n = 3 if method == BaseFCFMethod.THREE_YEAR_AVG else 5
    recent_low = valid_low[-n:]
    recent_high = valid_high[-n:]
    warnings = []
    if len(recent_low) < n:
        warnings.append(
            f"only {len(recent_low)} of {n} years available for {method.value} base owner earnings"
        )
    return (
        sum(recent_low) / len(recent_low),
        sum(recent_high) / len(recent_high),
        warnings,
    )


class OwnerEarningsDCFResult(BaseModel):
    base_owner_earnings_low: float
    base_owner_earnings_high: float
    dcf_from_low_base: DCFResult
    dcf_from_high_base: DCFResult
    value_per_share_low: float | None = None
    value_per_share_high: float | None = None
    warnings: list[str] = []


def run_owner_earnings_dcf_valuation(
    statements: list[FinancialStatement], assumptions: ValuationAssumptions
) -> OwnerEarningsDCFResult:
    """Same DCF mechanics as dcf.run_dcf_valuation() (project, discount,
    terminal value, sensitivity - dcf.py's functions are reused as-is,
    not reimplemented), but discounting owner earnings instead of FCFF.

    Runs the full DCF+sensitivity twice - once from the conservative
    base (owner_earnings_low) and once from the optimistic base
    (owner_earnings_high) - and takes the minimum value-per-share from
    the low run and the maximum from the high run. This combines both
    sources of uncertainty (the maintenance-capex assumption AND the
    usual discount-rate/terminal-growth sensitivity) into one range,
    rather than exposing a 2x3x3 grid.
    """
    if not statements:
        raise UnsupportedValuationError("no financial statements available")

    latest = max(statements, key=lambda s: s.period_end)
    if latest.company.valuation_category != ValuationCategory.STANDARD:
        raise UnsupportedValuationError(
            "Owner Earnings DCF is not supported for valuation_category="
            f"{latest.company.valuation_category.value}"
        )

    series = compute_owner_earnings_series(statements)
    base_low, base_high, base_warnings = select_base_owner_earnings(
        series, assumptions.base_fcf_method
    )
    if base_low is None or base_high is None:
        raise UnsupportedValuationError("insufficient data to compute base owner earnings")

    dcf_low = run_dcf(latest, base_low, assumptions)
    dcf_low.sensitivity = run_sensitivity(latest, base_low, assumptions)
    dcf_high = run_dcf(latest, base_high, assumptions)
    dcf_high.sensitivity = run_sensitivity(latest, base_high, assumptions)

    low_candidates = [
        v
        for v in [dcf_low.value_per_share] + [p.value_per_share for p in dcf_low.sensitivity]
        if v is not None
    ]
    high_candidates = [
        v
        for v in [dcf_high.value_per_share] + [p.value_per_share for p in dcf_high.sensitivity]
        if v is not None
    ]

    warnings = list(base_warnings) + dcf_low.warnings + dcf_high.warnings
    if not low_candidates or not high_candidates:
        warnings.append("insufficient data to compute a value-per-share range")

    return OwnerEarningsDCFResult(
        base_owner_earnings_low=base_low,
        base_owner_earnings_high=base_high,
        dcf_from_low_base=dcf_low,
        dcf_from_high_base=dcf_high,
        value_per_share_low=min(low_candidates) if low_candidates else None,
        value_per_share_high=max(high_candidates) if high_candidates else None,
        warnings=warnings,
    )
