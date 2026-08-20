import pytest

from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.owner_earnings import (
    compute_owner_earnings_series,
    run_owner_earnings_dcf_valuation,
    select_base_owner_earnings,
)

COMPANY = CompanyInfo(
    cik="0000320193",
    ticker="TST",
    name="Test Co",
    sic="3571",
    sic_description="Electronic Computers",
    valuation_category=ValuationCategory.STANDARD,
)

BANK = COMPANY.model_copy(update={"valuation_category": ValuationCategory.FINANCIAL})


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
    company: CompanyInfo = COMPANY,
    net_income=None,
    depreciation_amortization=None,
    capex=None,
    current_assets=None,
    current_liabilities=None,
    cash=None,
    short_term_debt=None,
    long_term_debt=None,
    shares_outstanding=None,
) -> FinancialStatement:
    def maybe(metric, value):
        return _fact(metric, value, period_end, fiscal_year) if value is not None else None

    return FinancialStatement(
        company=company,
        fiscal_year=fiscal_year,
        period_end=period_end,
        net_income=maybe("net_income", net_income),
        depreciation_amortization=maybe("depreciation_amortization", depreciation_amortization),
        capex=maybe("capex", capex),
        current_assets=maybe("current_assets", current_assets),
        current_liabilities=maybe("current_liabilities", current_liabilities),
        cash=maybe("cash", cash),
        short_term_debt=maybe("short_term_debt", short_term_debt),
        long_term_debt=maybe("long_term_debt", long_term_debt),
        shares_outstanding=maybe("shares_outstanding", shares_outstanding),
    )


def test_first_year_has_no_owner_earnings():
    statement = _statement(
        2023, "2023-12-31", net_income=800, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    )

    [result] = compute_owner_earnings_series([statement])

    assert result.change_in_nwc is None
    assert result.owner_earnings_low is None
    assert result.owner_earnings_high is None
    assert "no prior fiscal year available to compute change in NWC" in result.warnings


def test_owner_earnings_computed_for_second_year():
    year1 = _statement(
        2023, "2023-12-31", net_income=800, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    )
    year2 = _statement(
        2024, "2024-12-31", net_income=900, depreciation_amortization=120, capex=200,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
    )

    _, result2 = compute_owner_earnings_series([year1, year2])

    # NWC1 = (500-50)-(200-20) = 270; NWC2 = (600-80)-(250-30) = 300; delta = 30
    assert result2.change_in_nwc == 30
    # full-capex variant: 900 + 120 - 200 - 30 = 790
    assert result2.owner_earnings_full_capex_as_maintenance == 790
    # D&A-as-maintenance variant: 900 - 30 = 870 (D&A cancels)
    assert result2.owner_earnings_da_as_maintenance == 870
    assert result2.owner_earnings_low == 790
    assert result2.owner_earnings_high == 870
    assert result2.warnings == []


def test_owner_earnings_low_high_stays_correctly_ordered_when_capex_below_da():
    # Atypical case: capex < D&A (a shrinking/harvesting business) - the
    # "D&A as maintenance" variant would otherwise come out LOWER than
    # the "full capex as maintenance" variant, flipping the naive
    # low/high assumption. min()/max() must keep this ordered correctly.
    year1 = _statement(
        2023, "2023-12-31", net_income=800, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    )
    year2 = _statement(
        2024, "2024-12-31", net_income=900, depreciation_amortization=200, capex=50,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
    )

    _, result2 = compute_owner_earnings_series([year1, year2])

    # full-capex variant: 900 + 200 - 50 - 30 = 1020
    # D&A-as-maintenance variant: 900 - 30 = 870
    assert result2.owner_earnings_full_capex_as_maintenance == 1020
    assert result2.owner_earnings_da_as_maintenance == 870
    assert result2.owner_earnings_low == 870
    assert result2.owner_earnings_high == 1020


def test_select_base_owner_earnings_averages_both_bounds():
    series = compute_owner_earnings_series(
        [
            _statement(2022, "2022-12-31", net_income=700, depreciation_amortization=90,
                       capex=100, current_assets=400, current_liabilities=180, cash=40,
                       short_term_debt=10),
            _statement(2023, "2023-12-31", net_income=800, depreciation_amortization=100,
                       capex=150, current_assets=500, current_liabilities=200, cash=50,
                       short_term_debt=20),
            _statement(2024, "2024-12-31", net_income=900, depreciation_amortization=120,
                       capex=200, current_assets=600, current_liabilities=250, cash=80,
                       short_term_debt=30),
        ]
    )

    low, high, warnings = select_base_owner_earnings(series, BaseFCFMethod.THREE_YEAR_AVG)

    # only FY2023/FY2024 are computable (FY2022 is the first year)
    assert low == pytest.approx((series[1].owner_earnings_low + series[2].owner_earnings_low) / 2)
    assert high == pytest.approx(
        (series[1].owner_earnings_high + series[2].owner_earnings_high) / 2
    )
    assert any("only 2 of 3 years available" in w for w in warnings)


def test_select_base_owner_earnings_no_valid_years_returns_none():
    series = compute_owner_earnings_series(
        [_statement(2023, "2023-12-31", net_income=800)]
    )

    low, high, warnings = select_base_owner_earnings(series, BaseFCFMethod.LATEST_YEAR)

    assert low is None
    assert high is None
    assert warnings == ["no fiscal year has computable owner earnings"]


def _two_year_statements(company=COMPANY):
    return [
        _statement(
            2023, "2023-12-31", company=company, net_income=800,
            depreciation_amortization=100, capex=150, current_assets=500,
            current_liabilities=200, cash=50, short_term_debt=20, long_term_debt=300,
            shares_outstanding=100,
        ),
        _statement(
            2024, "2024-12-31", company=company, net_income=900,
            depreciation_amortization=120, capex=200, current_assets=600,
            current_liabilities=250, cash=80, short_term_debt=30, long_term_debt=320,
            shares_outstanding=100,
        ),
    ]


def _assumptions(**overrides):
    base = {
        "fcff_growth_rate": 0.05,
        "discount_rate": 0.10,
        "terminal_growth_rate": 0.03,
        "tax_rate": 0.25,
        "forecast_years": 3,
    }
    base.update(overrides)
    return ValuationAssumptions(**base)


def test_run_owner_earnings_dcf_valuation_produces_ordered_range():
    result = run_owner_earnings_dcf_valuation(_two_year_statements(), _assumptions())

    assert result.value_per_share_low is not None
    assert result.value_per_share_high is not None
    assert result.value_per_share_low <= result.value_per_share_high
    assert result.base_owner_earnings_low <= result.base_owner_earnings_high
    # the low run's own base value_per_share should be <= the high run's
    assert result.dcf_from_low_base.base_fcff == result.base_owner_earnings_low
    assert result.dcf_from_high_base.base_fcff == result.base_owner_earnings_high


def test_run_owner_earnings_dcf_valuation_rejects_financial_company():
    with pytest.raises(UnsupportedValuationError):
        run_owner_earnings_dcf_valuation(_two_year_statements(company=BANK), _assumptions())


def test_run_owner_earnings_dcf_valuation_rejects_empty_statements():
    with pytest.raises(UnsupportedValuationError):
        run_owner_earnings_dcf_valuation([], _assumptions())
