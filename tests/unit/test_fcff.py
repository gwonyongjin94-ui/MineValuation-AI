from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.assumptions import BaseFCFMethod
from app.valuation.fcff import compute_fcff_series, compute_operating_nwc, select_base_fcff

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
    )


def test_operating_nwc_excludes_cash_and_short_term_debt():
    statement = _statement(
        2024, "2024-12-31", current_assets=500, current_liabilities=200, cash=50,
        short_term_debt=20,
    )

    nwc, warnings = compute_operating_nwc(statement)

    assert nwc == 270  # (500-50) - (200-20)
    assert warnings == []


def test_operating_nwc_missing_short_term_debt_defaults_to_zero_with_warning():
    statement = _statement(
        2024, "2024-12-31", current_assets=500, current_liabilities=200, cash=50,
    )

    nwc, warnings = compute_operating_nwc(statement)

    assert nwc == 250  # (500-50) - (200-0)
    assert "short_term_debt not found - assumed 0 for non-cash NWC calc" in warnings


def test_operating_nwc_missing_balance_sheet_field_returns_none():
    statement = _statement(2024, "2024-12-31", current_liabilities=200, cash=50)

    nwc, warnings = compute_operating_nwc(statement)

    assert nwc is None
    assert warnings == []


def test_fcff_series_first_year_has_no_change_in_nwc():
    statement = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    )

    [result] = compute_fcff_series([statement], tax_rate=0.25)

    assert result.change_in_nwc is None
    assert result.fcff is None
    assert "no prior fiscal year available to compute change in NWC" in result.warnings
    assert "insufficient inputs to compute FCFF for this fiscal year" in result.warnings


def test_fcff_computed_correctly_for_second_year():
    year1 = _statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    )
    year2 = _statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
    )

    _, result2 = compute_fcff_series([year1, year2], tax_rate=0.25)

    # NWC1 = (500-50)-(200-20) = 270; NWC2 = (600-80)-(250-30) = 300; delta = 30
    # NOPAT2 = 1200 * 0.75 = 900; FCFF2 = 900 + 120 - 180 - 30 = 810
    assert result2.change_in_nwc == 30
    assert result2.nopat == 900
    assert result2.fcff == 810
    assert result2.warnings == []


def test_select_base_fcff_latest_year():
    fcff_series = compute_fcff_series(
        [
            _statement(2022, "2022-12-31", operating_income=900, depreciation_amortization=90,
                       capex=100, current_assets=400, current_liabilities=180, cash=40,
                       short_term_debt=10),
            _statement(2023, "2023-12-31", operating_income=1000, depreciation_amortization=100,
                       capex=150, current_assets=500, current_liabilities=200, cash=50,
                       short_term_debt=20),
            _statement(2024, "2024-12-31", operating_income=1200, depreciation_amortization=120,
                       capex=180, current_assets=600, current_liabilities=250, cash=80,
                       short_term_debt=30),
        ],
        tax_rate=0.25,
    )

    base, warnings = select_base_fcff(fcff_series, BaseFCFMethod.LATEST_YEAR)

    assert base == fcff_series[-1].fcff
    assert warnings == []


def test_select_base_fcff_3yr_avg_warns_when_insufficient_years():
    fcff_series = compute_fcff_series(
        [
            _statement(2023, "2023-12-31", operating_income=1000, depreciation_amortization=100,
                       capex=150, current_assets=500, current_liabilities=200, cash=50,
                       short_term_debt=20),
            _statement(2024, "2024-12-31", operating_income=1200, depreciation_amortization=120,
                       capex=180, current_assets=600, current_liabilities=250, cash=80,
                       short_term_debt=30),
        ],
        tax_rate=0.25,
    )

    base, warnings = select_base_fcff(fcff_series, BaseFCFMethod.THREE_YEAR_AVG)

    # only fy2024 has a computable FCFF (fy2023 is the first year, no prior NWC)
    assert base == fcff_series[1].fcff
    assert any("only 1 of 3 years available" in w for w in warnings)


def test_select_base_fcff_no_valid_years_returns_none():
    fcff_series = compute_fcff_series(
        [_statement(2023, "2023-12-31", operating_income=1000)], tax_rate=0.25
    )

    base, warnings = select_base_fcff(fcff_series, BaseFCFMethod.LATEST_YEAR)

    assert base is None
    assert warnings == ["no fiscal year has a computable FCFF"]
