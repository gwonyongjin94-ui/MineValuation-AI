import pytest

from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.growth import estimate_fundamental_growth_rate

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
    operating_income=None,
    depreciation_amortization=None,
    capex=None,
    current_assets=None,
    current_liabilities=None,
    cash=None,
    short_term_debt=None,
    long_term_debt=None,
    stockholders_equity=None,
    shares_outstanding=None,
) -> FinancialStatement:
    def maybe(metric, value):
        return _fact(metric, value, period_end, fiscal_year) if value is not None else None

    return FinancialStatement(
        company=COMPANY,
        fiscal_year=fiscal_year,
        period_end=period_end,
        operating_income=maybe("operating_income", operating_income),
        depreciation_amortization=maybe("depreciation_amortization", depreciation_amortization),
        capex=maybe("capex", capex),
        current_assets=maybe("current_assets", current_assets),
        current_liabilities=maybe("current_liabilities", current_liabilities),
        cash=maybe("cash", cash),
        short_term_debt=maybe("short_term_debt", short_term_debt),
        long_term_debt=maybe("long_term_debt", long_term_debt),
        stockholders_equity=maybe("stockholders_equity", stockholders_equity),
        shares_outstanding=maybe("shares_outstanding", shares_outstanding),
    )


def test_growth_rate_computed_correctly_for_second_year():
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        long_term_debt=100, stockholders_equity=1000,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        long_term_debt=120, stockholders_equity=1100,
    )

    result = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.25)

    # From test_fcff.py's identical inputs: NOPAT2=900, change_in_nwc=30, capex=180, d&a=120
    # reinvestment_rate = (180 - 120 + 30) / 900 = 0.1
    # invested_capital = 30 + 120 + 1100 - 80 = 1170; roic = 900 / 1170
    year2_result = result.by_year[1]
    assert year2_result.reinvestment_rate == 0.1
    assert year2_result.roic == 900 / 1170
    assert year2_result.growth_rate == 0.1 * (900 / 1170)
    assert year2_result.warnings == []

    # year1 has no prior-year NWC, so FCFF's change_in_nwc is None
    assert result.by_year[0].growth_rate is None
    assert "insufficient FCFF inputs to compute reinvestment rate" in result.by_year[0].warnings


def test_suggested_growth_rate_averages_available_years_and_warns_when_short():
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        long_term_debt=100, stockholders_equity=1000,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        long_term_debt=120, stockholders_equity=1100,
    )

    result = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.25)

    # only fy2024 has a computable growth_rate (fy2023 is the first year)
    assert result.suggested_growth_rate == result.by_year[1].growth_rate
    assert result.years_averaged == 1
    assert any("only 1 of 3 years available" in w for w in result.warnings)


def test_no_computable_years_returns_none_with_warning():
    result = estimate_fundamental_growth_rate(
        [_statement(2023, "2023-12-31", operating_income=1000)], tax_rate=0.25
    )

    assert result.suggested_growth_rate is None
    assert result.years_averaged == 0
    assert result.warnings == ["no fiscal year has a computable fundamental growth rate"]


def test_missing_debt_defaults_to_zero_with_warning():
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        stockholders_equity=1000,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        stockholders_equity=1100,
    )

    result = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.25)

    # invested_capital = 30 + 0 + 1100 - 80 = 1050 (long_term_debt defaulted to 0)
    year2_result = result.by_year[1]
    assert year2_result.roic == 900 / 1050
    assert "long_term_debt not found - assumed 0 for invested capital" in year2_result.warnings


def test_negative_reinvestment_and_roic_warns_about_misleading_positive_growth():
    # Reproduces the real CRCL (Circle Internet Group) case: a negative NOPAT
    # gives a negative ROIC, and a positive net-capex+NWC swing over a
    # negative NOPAT gives a negative reinvestment rate - the two negatives
    # multiply to a *positive* growth_rate that looks like healthy growth
    # but actually describes a shrinking, negative-return business.
    year1 = _statement(
        2023, "2023-12-31", operating_income=-50, depreciation_amortization=20, capex=50,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        stockholders_equity=500,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=-100, depreciation_amortization=20, capex=100,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        stockholders_equity=500,
    )

    result = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.0)

    # NWC is identical both years (same current_assets/liabilities/cash/
    # short_term_debt), so change_in_nwc=0: reinvestment_rate = (100-20+0)/-100 = -0.8
    # invested_capital = 20 + 0 (defaulted) + 500 - 50 = 470; roic = -100/470
    year2_result = result.by_year[1]
    assert year2_result.reinvestment_rate == -0.8
    assert year2_result.roic == pytest.approx(-100 / 470)
    assert year2_result.growth_rate == pytest.approx(-0.8 * (-100 / 470))
    assert year2_result.growth_rate > 0
    assert any(
        "reinvestment_rate and ROIC are both negative" in w for w in year2_result.warnings
    )


def test_market_value_equity_used_for_latest_year_when_book_equity_is_near_zero():
    # Reproduces the real HD/BA case: book equity crushed near-zero by
    # buybacks/leverage makes book-based ROIC explode to an implausible
    # 1200%. Passing market_price swaps in market-value equity
    # (price x shares_outstanding) for the LATEST year only.
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        long_term_debt=100, stockholders_equity=10,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        long_term_debt=120, stockholders_equity=5, shares_outstanding=100,
    )

    without_price = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.25)
    year2_book = without_price.by_year[1]
    # invested_capital = 30+120+5-80 = 75; roic = 900/75 = 12.0 (1200%) - the bug
    assert year2_book.roic == pytest.approx(900 / 75)
    assert year2_book.roic > 1.0

    with_price = estimate_fundamental_growth_rate(
        [year1, year2], tax_rate=0.25, market_price=50.0
    )
    year2_market = with_price.by_year[1]
    # invested_capital = 30+120+(50*100)-80 = 5070; roic = 900/5070
    assert year2_market.roic == pytest.approx(900 / 5070)
    assert year2_market.roic < 1.0
    assert any("market value of equity" in w for w in year2_market.warnings)

    # earlier years are unaffected - still None either way (year1 has no
    # prior-year NWC regardless of market_price)
    assert with_price.by_year[0].growth_rate is None


def test_market_price_ignored_when_shares_outstanding_missing():
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        long_term_debt=100, stockholders_equity=1000,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        long_term_debt=120, stockholders_equity=1100,
    )

    result = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.25, market_price=50.0)

    # no shares_outstanding on year2 -> falls back to book equity:
    # invested_capital = 30+120+1100-80 = 1170; roic = 900/1170
    year2_result = result.by_year[1]
    assert year2_result.roic == pytest.approx(900 / 1170)
    assert not any("market value of equity" in w for w in year2_result.warnings)


def test_negative_invested_capital_skips_roic():
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        long_term_debt=0, stockholders_equity=-500,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        long_term_debt=0, stockholders_equity=-500,
    )

    result = estimate_fundamental_growth_rate([year1, year2], tax_rate=0.25)

    year2_result = result.by_year[1]
    assert year2_result.reinvestment_rate is not None
    assert year2_result.roic is None
    assert year2_result.growth_rate is None
    assert "invested capital is zero or negative - ROIC is not meaningful" in year2_result.warnings
