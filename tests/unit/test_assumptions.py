import pytest

from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions


def test_valid_assumptions_construct():
    assumptions = ValuationAssumptions(
        fcff_growth_rate=0.05,
        discount_rate=0.09,
        terminal_growth_rate=0.025,
        tax_rate=0.21,
    )

    assert assumptions.forecast_years == 5
    assert assumptions.base_fcf_method == BaseFCFMethod.THREE_YEAR_AVG


def test_terminal_growth_must_be_below_discount_rate():
    with pytest.raises(ValueError, match="terminal_growth_rate must be less than discount_rate"):
        ValuationAssumptions(
            fcff_growth_rate=0.05,
            discount_rate=0.08,
            terminal_growth_rate=0.08,
            tax_rate=0.21,
        )
