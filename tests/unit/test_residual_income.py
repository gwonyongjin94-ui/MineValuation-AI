import pytest

from app.data.models import CompanyInfo, FinancialStatement, ValuationCategory
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.residual_income import (
    project_residual_income,
    run_residual_income,
    run_residual_income_estimate,
    run_residual_income_valuation,
)
from tests.factories import DEFAULT_COMPANY, make_statement


def test_project_residual_income_matches_hand_calculation():
    net_income, book_value, residual_income = project_residual_income(
        book_value=1000.0, net_income_base=100.0, growth_rate=0.05, cost_of_equity=0.10, years=3
    )

    assert net_income == [pytest.approx(105.0), pytest.approx(110.25), pytest.approx(115.7625)]
    assert book_value == [pytest.approx(1105.0), pytest.approx(1215.25), pytest.approx(1331.0125)]
    # RI(t) = NI(t) - cost_of_equity * BV(t-1): year 1 is NI above the
    # 10%-of-book-value bar (105 - 100 = 5), but book value compounds
    # faster than net income here, so RI turns negative from year 2 on.
    assert residual_income == [
        pytest.approx(5.0), pytest.approx(-0.25), pytest.approx(-5.7625),
    ]


def test_run_residual_income_matches_hand_calculation():
    statement = make_statement(2024, "2024-12-31", shares_outstanding=100)
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21,
        forecast_years=3,
    )

    result = run_residual_income(
        statement, book_value=1000.0, net_income_base=100.0, cost_of_equity=0.10,
        assumptions=assumptions,
    )

    assert result.discounted_residual_income == [
        pytest.approx(4.545454545), pytest.approx(-0.206611570), pytest.approx(-4.329451540),
    ]
    assert result.terminal_residual_income == pytest.approx(-84.791071429)
    assert result.discounted_terminal_residual_income == pytest.approx(-63.704786949)
    assert result.equity_value == pytest.approx(936.304604486)
    assert result.value_per_share == pytest.approx(9.363046045)


def test_run_residual_income_raises_when_cost_of_equity_at_or_below_terminal_growth():
    statement = make_statement(2024, "2024-12-31", shares_outstanding=100)
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="cost_of_equity must exceed"):
        run_residual_income(
            statement, book_value=1000.0, net_income_base=100.0, cost_of_equity=0.03,
            assumptions=assumptions,
        )


def test_run_residual_income_missing_shares_warns_and_skips_value_per_share():
    statement = make_statement(2024, "2024-12-31")
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    result = run_residual_income(
        statement, book_value=1000.0, net_income_base=100.0, cost_of_equity=0.10,
        assumptions=assumptions,
    )

    assert result.value_per_share is None
    assert "shares_outstanding not found - cannot compute value per share" in result.warnings


def test_run_residual_income_valuation_raises_for_financial_company():
    company = CompanyInfo(
        cik="0000019617", ticker="JPM", name="Test Bank", sic="6021",
        sic_description="National Commercial Banks", valuation_category=ValuationCategory.FINANCIAL,
    )
    statement = FinancialStatement(company=company, fiscal_year=2024, period_end="2024-12-31")
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="valuation_category=financial"):
        run_residual_income_valuation([statement], assumptions, cost_of_equity=0.10)


def test_run_residual_income_valuation_raises_when_book_value_or_net_income_missing():
    statement = FinancialStatement(
        company=DEFAULT_COMPANY, fiscal_year=2024, period_end="2024-12-31"
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="insufficient data"):
        run_residual_income_valuation([statement], assumptions, cost_of_equity=0.10)


def test_run_residual_income_valuation_raises_for_negative_book_value():
    statement = make_statement(
        2024, "2024-12-31", stockholders_equity=-500, net_income=100, shares_outstanding=100
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21
    )

    with pytest.raises(UnsupportedValuationError, match="non-positive book value"):
        run_residual_income_valuation([statement], assumptions, cost_of_equity=0.10)


def test_run_residual_income_estimate_end_to_end_includes_sensitivity_range():
    statement = make_statement(
        2024, "2024-12-31", stockholders_equity=1000, net_income=100, shares_outstanding=100
    )
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05, discount_rate=0.10, terminal_growth_rate=0.03, tax_rate=0.21,
        forecast_years=3,
    )

    estimate = run_residual_income_estimate([statement], assumptions, cost_of_equity=0.10)

    assert estimate.result.book_value == 1000
    assert estimate.result.net_income_base == 100
    assert len(estimate.result.sensitivity) == 9
    assert estimate.value_per_share_low is not None
    assert estimate.value_per_share_high is not None
    assert estimate.value_per_share_low <= estimate.value_per_share_high
