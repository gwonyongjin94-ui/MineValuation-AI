import pytest

from app.data.models import CompanyInfo, FinancialStatement, ValuationCategory
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.dcf import (
    UnsupportedValuationError,
    run_dcf,
    run_dcf_valuation,
    run_sensitivity,
)
from tests.factories import DEFAULT_COMPANY, make_statement


def test_run_dcf_full_pipeline_matches_hand_calculation():
    statement = make_statement(
        2024, "2024-12-31", cash=500, short_term_debt=100, long_term_debt=400,
        shares_outstanding=1000,
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21,
        forecast_years=1,
    )

    result = run_dcf(statement, base_fcff=1000, assumptions=assumptions)

    assert result.projected_fcff == [pytest.approx(1050)]
    assert result.discounted_fcff == [pytest.approx(954.545454545)]
    assert result.terminal_value == pytest.approx(15450.0)
    assert result.discounted_terminal_value == pytest.approx(14045.454545)
    assert result.enterprise_value == pytest.approx(15000.0)
    assert result.total_debt == 500
    assert result.equity_value == pytest.approx(15000.0)
    assert result.value_per_share == pytest.approx(15.0)
    assert any("terminal value is" in w for w in result.warnings)


def test_run_dcf_missing_cash_warns_and_skips_equity_bridge():
    statement = make_statement(2024, "2024-12-31", shares_outstanding=1000)
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21,
        forecast_years=3,
    )

    result = run_dcf(statement, base_fcff=1000, assumptions=assumptions)

    assert result.equity_value is None
    assert result.value_per_share is None
    assert "cash not found - cannot bridge enterprise value to equity value" in result.warnings


def test_run_dcf_missing_shares_warns_and_skips_value_per_share():
    statement = make_statement(2024, "2024-12-31", cash=500)
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21,
        forecast_years=3,
    )

    result = run_dcf(statement, base_fcff=1000, assumptions=assumptions)

    assert result.equity_value is not None
    assert result.value_per_share is None
    assert "shares_outstanding not found - cannot compute value per share" in result.warnings


def test_run_sensitivity_produces_grid_and_skips_invalid_combinations():
    statement = make_statement(
        2024, "2024-12-31", cash=500, short_term_debt=100, long_term_debt=400,
        shares_outstanding=1000,
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.02, terminal_growth_rate=0.015, tax_rate=0.21,
        forecast_years=3,
    )

    points = run_sensitivity(statement, base_fcff=1000, assumptions=assumptions)

    assert len(points) == 9
    invalid = [p for p in points if p.value_per_share is None]
    valid = [p for p in points if p.value_per_share is not None]
    assert len(invalid) == 3
    assert len(valid) == 6


def test_run_dcf_valuation_raises_for_financial_company():
    company = CompanyInfo(
        cik="0000019617", ticker="JPM", name="Test Bank", sic="6021",
        sic_description="National Commercial Banks", valuation_category=ValuationCategory.FINANCIAL,
    )
    statement = FinancialStatement(
        company=company, fiscal_year=2024, period_end="2024-12-31"
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="valuation_category=financial"):
        run_dcf_valuation([statement], assumptions)


def test_run_dcf_valuation_raises_when_no_statements():
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="no financial statements"):
        run_dcf_valuation([], assumptions)


def test_run_dcf_valuation_raises_when_fcff_uncomputable():
    statement = FinancialStatement(
        company=DEFAULT_COMPANY, fiscal_year=2024, period_end="2024-12-31"
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="insufficient data"):
        run_dcf_valuation([statement], assumptions)


def test_run_dcf_valuation_end_to_end_includes_sensitivity():
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

    result = run_dcf_valuation([year1, year2], assumptions)

    assert result.base_fcff == 810
    assert len(result.sensitivity) == 9
    assert result.value_per_share is not None
