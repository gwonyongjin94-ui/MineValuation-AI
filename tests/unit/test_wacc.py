import pytest

from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory
from app.valuation.wacc import (
    EQUITY_RISK_PREMIUM,
    FALLBACK_UNLEVERED_BETA,
    estimate_wacc,
    unlevered_beta_for_sic,
)


def _company(sic: str) -> CompanyInfo:
    return CompanyInfo(
        cik="0000320193",
        ticker="TST",
        name="Test Co",
        sic=sic,
        sic_description="Test Industry",
        valuation_category=ValuationCategory.STANDARD,
    )


def _fact(value: float) -> FinancialFact:
    return FinancialFact(
        metric="x",
        value=value,
        unit="USD",
        taxonomy="us-gaap",
        xbrl_tag="SomeTag",
        period_start="2023-01-01",
        period_end="2023-12-31",
        fiscal_year=2023,
        fiscal_period="FY",
        form="10-K",
        filed_date="2024-02-01",
        accession_number="ACCN-2023",
    )


def _statement(
    sic: str = "7372",
    *,
    operating_income=None,
    interest_expense=None,
    short_term_debt=None,
    long_term_debt=None,
    shares_outstanding=None,
) -> FinancialStatement:
    def maybe(value):
        return _fact(value) if value is not None else None

    return FinancialStatement(
        company=_company(sic),
        fiscal_year=2023,
        period_end="2023-12-31",
        operating_income=maybe(operating_income),
        interest_expense=maybe(interest_expense),
        short_term_debt=maybe(short_term_debt),
        long_term_debt=maybe(long_term_debt),
        shares_outstanding=maybe(shares_outstanding),
    )


def test_unlevered_beta_for_sic_matches_known_prefix():
    label, beta = unlevered_beta_for_sic("7372")
    assert label == "Software (System & Application)"
    assert beta == 1.23


def test_unlevered_beta_for_sic_falls_back_when_unmatched():
    label, beta = unlevered_beta_for_sic("9999")
    assert beta == FALLBACK_UNLEVERED_BETA
    assert "no SIC match" in label


def test_unlevered_beta_for_sic_falls_back_when_none():
    _label, beta = unlevered_beta_for_sic(None)
    assert beta == FALLBACK_UNLEVERED_BETA


def test_estimate_wacc_full_computation():
    statement = _statement(
        sic="7372",  # Software (System & Application), unlevered beta 1.23
        operating_income=1_000_000_000,
        interest_expense=50_000_000,  # coverage = 20 -> Aaa/AAA bucket
        short_term_debt=100_000_000,
        long_term_debt=900_000_000,
        shares_outstanding=100_000_000,
    )

    result = estimate_wacc(statement, market_price=50.0, risk_free_rate=0.04, tax_rate=0.21)

    market_equity = 50.0 * 100_000_000  # 5,000,000,000
    debt = 1_000_000_000
    expected_levered_beta = 1.23 * (1 + (1 - 0.21) * (debt / market_equity))
    assert result.levered_beta == pytest.approx(expected_levered_beta)

    expected_cost_of_equity = 0.04 + expected_levered_beta * EQUITY_RISK_PREMIUM
    assert result.cost_of_equity == pytest.approx(expected_cost_of_equity)

    assert result.synthetic_rating == "Aaa/AAA"
    expected_cost_of_debt_pretax = 0.04 + 0.0040
    assert result.cost_of_debt_pretax == pytest.approx(expected_cost_of_debt_pretax)
    expected_cost_of_debt_aftertax = expected_cost_of_debt_pretax * (1 - 0.21)
    assert result.cost_of_debt_aftertax == pytest.approx(expected_cost_of_debt_aftertax)

    total_capital = market_equity + debt
    expected_wacc = (market_equity / total_capital) * expected_cost_of_equity + (
        debt / total_capital
    ) * expected_cost_of_debt_aftertax
    assert result.wacc == pytest.approx(expected_wacc)
    assert result.warnings == []


def test_estimate_wacc_zero_debt_wacc_equals_cost_of_equity():
    statement = _statement(
        sic="7372",
        operating_income=1_000_000_000,
        interest_expense=None,
        short_term_debt=None,
        long_term_debt=None,
        shares_outstanding=100_000_000,
    )

    result = estimate_wacc(statement, market_price=50.0, risk_free_rate=0.04, tax_rate=0.21)

    assert result.wacc == pytest.approx(result.cost_of_equity)


def test_estimate_wacc_missing_shares_outstanding_uses_unlevered_beta_and_warns():
    statement = _statement(sic="7372", operating_income=1_000_000_000, interest_expense=50_000_000)

    result = estimate_wacc(statement, market_price=50.0, risk_free_rate=0.04, tax_rate=0.21)

    assert result.levered_beta == result.unlevered_beta
    assert result.wacc is None
    assert any("shares_outstanding not found" in w for w in result.warnings)
    assert any("cannot compute capital-structure weights" in w for w in result.warnings)


def test_estimate_wacc_missing_interest_expense_skips_cost_of_debt():
    statement = _statement(
        sic="7372",
        operating_income=1_000_000_000,
        long_term_debt=500_000_000,
        shares_outstanding=100_000_000,
    )

    result = estimate_wacc(statement, market_price=50.0, risk_free_rate=0.04, tax_rate=0.21)

    assert result.cost_of_debt_aftertax is None
    assert result.wacc is None
    assert any("cannot estimate cost of debt" in w for w in result.warnings)


def test_estimate_wacc_low_coverage_gets_junk_rating():
    statement = _statement(
        sic="3721",  # Aerospace/Defense
        operating_income=100_000_000,
        interest_expense=90_000_000,  # coverage ~1.11 -> Caa/CCC bucket
        long_term_debt=2_000_000_000,
        shares_outstanding=50_000_000,
    )

    result = estimate_wacc(statement, market_price=200.0, risk_free_rate=0.04, tax_rate=0.21)

    assert result.synthetic_rating == "Caa/CCC"
    assert result.cost_of_debt_pretax == pytest.approx(0.04 + 0.0885)
