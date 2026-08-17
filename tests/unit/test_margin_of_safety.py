from datetime import date

import pytest

from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.margin_of_safety import _margin_of_safety, compute_margin_of_safety
from tests.factories import make_statement


def test_margin_of_safety_formula():
    assert _margin_of_safety(100, 60) == pytest.approx(0.4)
    assert _margin_of_safety(100, 150) == pytest.approx(-0.5)
    assert _margin_of_safety(None, 50) is None
    assert _margin_of_safety(0, 50) is None
    assert _margin_of_safety(-10, 50) is None


def _three_year_statements():
    year0 = make_statement(
        2022, "2022-12-31", operating_income=900, depreciation_amortization=90, capex=100,
        current_assets=400, current_liabilities=180, cash=40, short_term_debt=10,
    )
    year1 = make_statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
        shares_outstanding=1000,
    )
    year2 = make_statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        shares_outstanding=1000,
    )
    return year0, year1, year2


def _assumptions(**overrides):
    base = {
        "fcff_growth_rate": 0.05,
        "discount_rate": 0.10,
        "terminal_growth_rate": 0.03,
        "tax_rate": 0.25,
        "forecast_years": 3,
        "base_fcf_method": BaseFCFMethod.LATEST_YEAR,
    }
    base.update(overrides)
    return ValuationAssumptions(**base)


def test_margin_of_safety_range_brackets_base_case():
    year0, year1, year2 = _three_year_statements()

    result = compute_margin_of_safety(
        [year0, year1, year2], _assumptions(), market_price=50, as_of_date=date(2026, 1, 1)
    )

    assert result.intrinsic_value_low <= result.intrinsic_value_per_share <= result.intrinsic_value_high
    assert result.margin_of_safety_low <= result.margin_of_safety <= result.margin_of_safety_high


def test_look_ahead_bias_excludes_future_filed_statements():
    # factory sets each fact's filed_date to "{fiscal_year+1}-02-01" -
    # year2 (fy2024) is filed 2025-02-01, after the as_of_date below.
    year0, year1, year2 = _three_year_statements()

    result = compute_margin_of_safety(
        [year0, year1, year2], _assumptions(), market_price=50, as_of_date=date(2024, 6, 1)
    )

    assert result.statements_used == 2
    assert result.statements_excluded_look_ahead == 1
    assert any("look-ahead" in w for w in result.warnings)


def test_raises_when_all_statements_excluded_by_look_ahead():
    year0, year1, year2 = _three_year_statements()

    with pytest.raises(UnsupportedValuationError, match="no financial statements filed on or before"):
        compute_margin_of_safety(
            [year0, year1, year2], _assumptions(), market_price=50, as_of_date=date(2000, 1, 1)
        )


def test_margin_of_safety_none_when_intrinsic_value_missing():
    year0, year1 = make_statement(
        2023, "2023-12-31", operating_income=1000, depreciation_amortization=100, capex=150,
        current_assets=500, current_liabilities=200, cash=50, short_term_debt=20,
    ), make_statement(
        2024, "2024-12-31", operating_income=1200, depreciation_amortization=120, capex=180,
        current_assets=600, current_liabilities=250, cash=80, short_term_debt=30,
        # no shares_outstanding -> DCF can't compute value_per_share
    )

    result = compute_margin_of_safety(
        [year0, year1], _assumptions(), market_price=50, as_of_date=date(2026, 1, 1)
    )

    assert result.intrinsic_value_per_share is None
    assert result.margin_of_safety is None
