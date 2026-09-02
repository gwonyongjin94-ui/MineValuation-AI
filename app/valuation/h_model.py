"""H-Model DCF (Fuller & Hsia, 1984, "A Simplified Common Stock
Valuation Model", Financial Analysts Journal 40(5), 49-56): FCFF growth
fades LINEARLY from fcff_growth_rate down to terminal_growth_rate over
the forecast period, instead of dcf.py's flat growth rate for
forecast_years followed by an overnight drop to terminal growth in
year N+1.

Real equity research doesn't assume a company grows at a constant rate
for exactly five years and then permanently downshifts overnight - the
H-model's linear fade is the standard practitioner fix for that cliff
(see the "DCF-시장가 괴리" research memo this was built to address).

Applied here to FCFF, not dividends (the H-model's original subject) -
a standard real-world adaptation: same fading-growth mechanics, just
discounting free cash flow to the firm instead of dividends per share.

Implemented as an explicit year-by-year projection (matching this
project's existing style in fcff.py/dcf.py - an auditable per-year
series, not a compressed closed-form shortcut) rather than the
H-model's original algebraic formula. Reuses dcf.py's discounting,
terminal-value, and equity-bridge logic unchanged via
run_dcf_from_projection() - the only thing that differs from a plain
DCF is how the projected FCFF series is built.
"""

from pydantic import BaseModel

from app.data.models import FinancialStatement, ValuationCategory
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import (
    DEFAULT_SENSITIVITY_DELTAS,
    DCFResult,
    SensitivityPoint,
    UnsupportedValuationError,
    run_dcf_from_projection,
    run_sensitivity_with,
)
from app.valuation.fcff import compute_fcff_series, select_base_fcff


def project_fading_fcff(
    base_fcff: float, start_growth_rate: float, terminal_growth_rate: float, years: int
) -> list[float]:
    """Year 1 grows at start_growth_rate, the final forecast year grows
    at terminal_growth_rate, and every year in between interpolates
    linearly between the two - so the last projected year is already
    growing at the same rate compute_terminal_value() then extends one
    more year into the perpetuity, with no growth-rate cliff at the
    forecast/terminal boundary (unlike project_fcff() + a flat rate).
    """
    projected = []
    fcff = base_fcff
    for t in range(1, years + 1):
        frac = (t - 1) / (years - 1) if years > 1 else 1.0
        growth_rate = start_growth_rate + (terminal_growth_rate - start_growth_rate) * frac
        fcff = fcff * (1 + growth_rate)
        projected.append(fcff)
    return projected


def run_h_model_dcf(
    statement: FinancialStatement, base_fcff: float, assumptions: ValuationAssumptions
) -> DCFResult:
    projected = project_fading_fcff(
        base_fcff,
        assumptions.fcff_growth_rate,
        assumptions.terminal_growth_rate,
        assumptions.forecast_years,
    )
    return run_dcf_from_projection(statement, base_fcff, projected, assumptions)


def run_h_model_sensitivity(
    statement: FinancialStatement,
    base_fcff: float,
    assumptions: ValuationAssumptions,
    discount_rate_deltas: tuple[float, ...] = DEFAULT_SENSITIVITY_DELTAS,
    terminal_growth_deltas: tuple[float, ...] = DEFAULT_SENSITIVITY_DELTAS,
) -> list[SensitivityPoint]:
    return run_sensitivity_with(
        run_h_model_dcf,
        statement,
        base_fcff,
        assumptions,
        discount_rate_deltas,
        terminal_growth_deltas,
    )


def run_h_model_valuation(
    statements: list[FinancialStatement], assumptions: ValuationAssumptions
) -> DCFResult:
    """Same shape as dcf.run_dcf_valuation() (base FCFF selection, the
    STANDARD-category guard, then DCF + sensitivity grid) - H-Model
    substitutes run_h_model_dcf/run_h_model_sensitivity for their flat-
    growth equivalents, nothing else changes.
    """
    if not statements:
        raise UnsupportedValuationError("no financial statements available")

    latest = max(statements, key=lambda s: s.period_end)
    if latest.company.valuation_category != ValuationCategory.STANDARD:
        raise UnsupportedValuationError(
            "H-Model DCF is not supported for valuation_category="
            f"{latest.company.valuation_category.value}"
        )

    fcff_series = compute_fcff_series(statements, assumptions.tax_rate)
    base_fcff, base_warnings = select_base_fcff(fcff_series, assumptions.base_fcf_method)
    if base_fcff is None:
        raise UnsupportedValuationError("insufficient data to compute a base FCFF")

    result = run_h_model_dcf(latest, base_fcff, assumptions)
    result.warnings = base_warnings + result.warnings
    result.sensitivity = run_h_model_sensitivity(latest, base_fcff, assumptions)
    return result


class HModelEstimate(BaseModel):
    dcf: DCFResult
    value_per_share_low: float | None = None
    value_per_share_high: float | None = None
    warnings: list[str] = []


def run_h_model_estimate(
    statements: list[FinancialStatement], assumptions: ValuationAssumptions
) -> HModelEstimate:
    dcf = run_h_model_valuation(statements, assumptions)
    valid = [p.value_per_share for p in dcf.sensitivity if p.value_per_share is not None]
    return HModelEstimate(
        dcf=dcf,
        value_per_share_low=min(valid) if valid else None,
        value_per_share_high=max(valid) if valid else None,
        warnings=dcf.warnings,
    )
