import pytest

from app.data.models import CompanyInfo, FinancialStatement, ValuationCategory
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError, run_dcf
from app.valuation.h_model import (
    project_fading_fcff,
    run_h_model_dcf,
    run_h_model_estimate,
    run_h_model_valuation,
)
from tests.factories import DEFAULT_COMPANY, make_statement


def test_project_fading_fcff_interpolates_linearly():
    # Year 1 at the start rate, final year at the terminal rate, exactly
    # halfway between at the midpoint year - the "no cliff" property
    # this module exists for.
    projected = project_fading_fcff(
        base_fcff=1000, start_growth_rate=0.05, terminal_growth_rate=0.03, years=3
    )

    assert projected == [pytest.approx(1050.0), pytest.approx(1092.0), pytest.approx(1124.76)]


def test_project_fading_fcff_single_year_uses_terminal_rate():
    # No midpoint to interpolate through when years=1 - falls straight
    # to the terminal rate rather than dividing by zero.
    projected = project_fading_fcff(
        base_fcff=1000, start_growth_rate=0.05, terminal_growth_rate=0.03, years=1
    )

    assert projected == [pytest.approx(1030.0)]


def test_run_h_model_dcf_matches_hand_calculation():
    statement = make_statement(
        2024, "2024-12-31", cash=500, short_term_debt=100, long_term_debt=400,
        shares_outstanding=1000,
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21,
        forecast_years=3,
    )

    result = run_h_model_dcf(statement, base_fcff=1000, assumptions=assumptions)

    assert result.projected_fcff == [
        pytest.approx(1050.0), pytest.approx(1092.0), pytest.approx(1124.76),
    ]
    assert result.discounted_fcff == [
        pytest.approx(954.545454545), pytest.approx(902.479338843), pytest.approx(845.048835462),
    ]
    assert result.terminal_value == pytest.approx(16550.04)
    assert result.discounted_terminal_value == pytest.approx(12434.290007513)
    assert result.enterprise_value == pytest.approx(15136.363636364)
    assert result.equity_value == pytest.approx(15136.363636364)
    assert result.value_per_share == pytest.approx(15.136363636)


def test_h_model_reduces_terminal_value_dominance_vs_flat_dcf():
    # The whole point of the linear fade: the final forecast year's FCFF
    # is smaller than a flat-growth projection's (since it grew toward
    # terminal_growth_rate, not fcff_growth_rate, in later years), so
    # the terminal value should be a smaller share of enterprise value
    # than the flat-rate DCF produces from the exact same inputs.
    statement = make_statement(
        2024, "2024-12-31", cash=500, short_term_debt=100, long_term_debt=400,
        shares_outstanding=1000,
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.08, discount_rate=0.09, terminal_growth_rate=0.025, tax_rate=0.21,
        forecast_years=5,
    )

    flat = run_dcf(statement, base_fcff=1000, assumptions=assumptions)
    faded = run_h_model_dcf(statement, base_fcff=1000, assumptions=assumptions)

    assert faded.terminal_value_pct_of_ev < flat.terminal_value_pct_of_ev


def test_run_h_model_valuation_raises_for_financial_company():
    company = CompanyInfo(
        cik="0000019617", ticker="JPM", name="Test Bank", sic="6021",
        sic_description="National Commercial Banks", valuation_category=ValuationCategory.FINANCIAL,
    )
    statement = FinancialStatement(company=company, fiscal_year=2024, period_end="2024-12-31")
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="valuation_category=financial"):
        run_h_model_valuation([statement], assumptions)


def test_run_h_model_valuation_raises_when_fcff_uncomputable():
    statement = FinancialStatement(
        company=DEFAULT_COMPANY, fiscal_year=2024, period_end="2024-12-31"
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="insufficient data"):
        run_h_model_valuation([statement], assumptions)


def test_run_h_model_estimate_end_to_end_includes_sensitivity_range():
    year1 = make_statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    )
    year2 = make_statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        shares_outstanding=1000,
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.25,
        forecast_years=3, base_fcf_method=BaseFCFMethod.LATEST_YEAR,
    )

    estimate = run_h_model_estimate([year1, year2], assumptions)

    assert estimate.dcf.base_fcff == 810
    assert len(estimate.dcf.sensitivity) == 9
    assert estimate.value_per_share_low is not None
    assert estimate.value_per_share_high is not None
    assert estimate.value_per_share_low <= estimate.value_per_share_high
