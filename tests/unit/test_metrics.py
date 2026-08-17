from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory
from app.financials.metrics import compute_metrics

COMPANY = CompanyInfo(
    cik="0000320193",
    ticker="TST",
    name="Test Co",
    sic="3571",
    sic_description="Electronic Computers",
    valuation_category=ValuationCategory.STANDARD,
)


def _fact(metric: str, value: float, period_end: str, fiscal_year: int) -> FinancialFact:
    return FinancialFact(
        metric=metric,
        value=value,
        unit="USD",
        taxonomy="us-gaap",
        xbrl_tag="SomeTag",
        period_start=f"{fiscal_year - 1}-01-01",
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        form="10-K",
        filed_date=f"{fiscal_year + 1}-02-01",
        accession_number=f"ACCN-{fiscal_year}",
    )


def _statement(
    fiscal_year: int,
    period_end: str,
    *,
    revenue=None,
    operating_income=None,
    net_income=None,
    operating_cash_flow=None,
    capex=None,
    depreciation_amortization=None,
    current_assets=None,
    current_liabilities=None,
) -> FinancialStatement:
    def maybe(metric, value):
        return _fact(metric, value, period_end, fiscal_year) if value is not None else None

    return FinancialStatement(
        company=COMPANY,
        fiscal_year=fiscal_year,
        period_end=period_end,
        revenue=maybe("revenue", revenue),
        operating_income=maybe("operating_income", operating_income),
        net_income=maybe("net_income", net_income),
        operating_cash_flow=maybe("operating_cash_flow", operating_cash_flow),
        capex=maybe("capex", capex),
        depreciation_amortization=maybe("depreciation_amortization", depreciation_amortization),
        current_assets=maybe("current_assets", current_assets),
        current_liabilities=maybe("current_liabilities", current_liabilities),
    )


def test_revenue_growth_between_adjacent_years():
    statements = [
        _statement(2023, "2023-12-31", revenue=100),
        _statement(2024, "2024-12-31", revenue=120),
    ]

    results = compute_metrics(statements)

    assert results[0].revenue_growth is None
    assert results[1].revenue_growth == 0.2


def test_margins_computed_correctly():
    statement = _statement(
        2024, "2024-12-31", revenue=1000, operating_income=200, net_income=100
    )

    [result] = compute_metrics([statement])

    assert result.operating_margin == 0.2
    assert result.net_margin == 0.1


def test_simple_fcf_and_fcf_margin():
    statement = _statement(
        2024, "2024-12-31", revenue=1000, operating_cash_flow=300, capex=50
    )

    [result] = compute_metrics([statement])

    assert result.simple_fcf == 250
    assert result.fcf_margin == 0.25


def test_ebitda_and_ebitda_margin():
    statement = _statement(
        2024, "2024-12-31", revenue=1000, operating_income=200, depreciation_amortization=80
    )

    [result] = compute_metrics([statement])

    assert result.ebitda == 280
    assert result.ebitda_margin == 0.28


def test_current_ratio():
    statement = _statement(2024, "2024-12-31", current_assets=500, current_liabilities=250)

    [result] = compute_metrics([statement])

    assert result.current_ratio == 2.0


def test_missing_fields_yield_none_not_zero():
    statement = _statement(2024, "2024-12-31", revenue=1000, net_income=50)

    [result] = compute_metrics([statement])

    assert result.operating_margin is None
    assert result.simple_fcf is None
    assert result.ebitda is None
    assert result.current_ratio is None
    assert result.net_margin == 0.05


def test_safety_flags_negative_fcf_and_low_current_ratio():
    statement = _statement(
        2024,
        "2024-12-31",
        revenue=1000,
        operating_income=-10,
        operating_cash_flow=20,
        capex=50,
        current_assets=80,
        current_liabilities=100,
    )

    [result] = compute_metrics([statement])

    assert "negative free cash flow (OCF - CapEx)" in result.warnings
    assert "negative operating margin" in result.warnings
    assert any("current ratio below 1.0" in w for w in result.warnings)


def test_no_flags_for_healthy_company():
    statement = _statement(
        2024,
        "2024-12-31",
        revenue=1000,
        operating_income=200,
        operating_cash_flow=300,
        capex=50,
        current_assets=500,
        current_liabilities=250,
    )

    [result] = compute_metrics([statement])

    assert result.warnings == []
