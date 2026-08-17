"""Pure financial-analysis metrics computed from normalized FinancialStatement data.

Financial Analysis != Valuation: nothing here uses a discount rate, a
growth assumption, or any forward-looking estimate - only arithmetic on
already-reported facts.

Net debt / leverage ratios are intentionally left out: the schema has no
long-term-debt field (Phase 1.5 never spiked a tag for it), and guessing
one now would repeat exactly the mistake this project has avoided so
far. Add it properly (spike -> schema -> normalizer) before computing it
here.
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import FinancialStatement


class YearMetrics(BaseModel):
    fiscal_year: int
    period_end: date
    revenue: float | None = None
    revenue_growth: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    simple_fcf: float | None = None
    fcf_margin: float | None = None
    ebitda: float | None = None
    ebitda_margin: float | None = None
    current_ratio: float | None = None
    warnings: list[str] = []


def compute_metrics(statements: list[FinancialStatement]) -> list[YearMetrics]:
    ordered = sorted(statements, key=lambda s: s.period_end)
    results = []

    for i, statement in enumerate(ordered):
        revenue = _value(statement.revenue)
        operating_income = _value(statement.operating_income)
        net_income = _value(statement.net_income)
        ocf = _value(statement.operating_cash_flow)
        capex = _value(statement.capex)
        d_and_a = _value(statement.depreciation_amortization)
        current_assets = _value(statement.current_assets)
        current_liabilities = _value(statement.current_liabilities)

        simple_fcf = ocf - capex if ocf is not None and capex is not None else None
        ebitda = (
            operating_income + d_and_a
            if operating_income is not None and d_and_a is not None
            else None
        )

        revenue_growth = None
        if i > 0:
            prev_revenue = _value(ordered[i - 1].revenue)
            if revenue is not None and prev_revenue:
                revenue_growth = (revenue - prev_revenue) / prev_revenue

        metrics = YearMetrics(
            fiscal_year=statement.fiscal_year,
            period_end=statement.period_end,
            revenue=revenue,
            revenue_growth=revenue_growth,
            operating_margin=_safe_div(operating_income, revenue),
            net_margin=_safe_div(net_income, revenue),
            simple_fcf=simple_fcf,
            fcf_margin=_safe_div(simple_fcf, revenue),
            ebitda=ebitda,
            ebitda_margin=_safe_div(ebitda, revenue),
            current_ratio=_safe_div(current_assets, current_liabilities),
        )
        metrics.warnings = _safety_flags(metrics)
        results.append(metrics)

    return results


def _value(fact) -> float | None:
    return fact.value if fact is not None else None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _safety_flags(metrics: YearMetrics) -> list[str]:
    flags = []
    if metrics.simple_fcf is not None and metrics.simple_fcf < 0:
        flags.append("negative free cash flow (OCF - CapEx)")
    if metrics.operating_margin is not None and metrics.operating_margin < 0:
        flags.append("negative operating margin")
    if metrics.current_ratio is not None and metrics.current_ratio < 1.0:
        flags.append(f"current ratio below 1.0 ({metrics.current_ratio:.2f})")
    return flags
