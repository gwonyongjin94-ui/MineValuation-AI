from datetime import date

import pytest

from app.data.models import FinancialStatement
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.margin_of_safety import (
    _margin_of_safety,
    _resolve_fact_as_of,
    _resolve_statement_as_of,
    compute_margin_of_safety,
)
from tests.factories import DEFAULT_COMPANY, make_fact, make_statement


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


# --- restatement-aware as-of-date resolution ---
# HBB's FY2019 revenue: $612,843,000 as originally reported (10-K, filed
# 2020-02-26), restated to $611,786,000 in a 10-K/A filed 2020-07-24 - the
# real numbers from docs/DATA_SPIKE_NOTES.md, reused here as the shape of
# a genuine restatement.


def test_resolve_fact_as_of_uses_as_reported_before_restatement_filed():
    as_reported = make_fact(
        "revenue", 612843000, "2019-12-31", 2019, filed_date="2020-02-26", accession_number="ORIG"
    )
    restated = make_fact(
        "revenue", 611786000, "2019-12-31", 2019, filed_date="2020-07-24", form="10-K/A",
        accession_number="AMEND",
    )

    resolved = _resolve_fact_as_of(as_reported, [restated], date(2020, 6, 1))

    assert resolved.value == 612843000


def test_resolve_fact_as_of_uses_restated_value_once_public():
    as_reported = make_fact(
        "revenue", 612843000, "2019-12-31", 2019, filed_date="2020-02-26", accession_number="ORIG"
    )
    restated = make_fact(
        "revenue", 611786000, "2019-12-31", 2019, filed_date="2020-07-24", form="10-K/A",
        accession_number="AMEND",
    )

    resolved = _resolve_fact_as_of(as_reported, [restated], date(2020, 8, 1))

    assert resolved.value == 611786000


def test_resolve_fact_as_of_none_before_anything_filed():
    as_reported = make_fact(
        "revenue", 612843000, "2019-12-31", 2019, filed_date="2020-02-26", accession_number="ORIG"
    )

    resolved = _resolve_fact_as_of(as_reported, [], date(2019, 12, 31))

    assert resolved is None


def test_resolve_statement_as_of_excludes_statement_with_nothing_public_yet():
    statement = make_statement(2019, "2019-12-31", revenue=612843000)

    resolved = _resolve_statement_as_of(statement, date(2000, 1, 1))

    assert resolved is None


def _statement_with_restated_operating_income() -> FinancialStatement:
    original = make_fact(
        "operating_income", 1000, "2019-12-31", 2019, filed_date="2020-02-26",
        accession_number="ORIG",
    )
    restated = make_fact(
        "operating_income", 800, "2019-12-31", 2019, filed_date="2020-07-24", form="10-K/A",
        accession_number="AMEND",
    )
    shared = {"filed_date": "2020-02-26"}
    return FinancialStatement(
        company=DEFAULT_COMPANY,
        fiscal_year=2019,
        period_end="2019-12-31",
        operating_income=original,
        depreciation_amortization=make_fact(
            "depreciation_amortization", 100, "2019-12-31", 2019, **shared
        ),
        capex=make_fact("capex", 150, "2019-12-31", 2019, **shared),
        current_assets=make_fact("current_assets", 500, "2019-12-31", 2019, **shared),
        current_liabilities=make_fact("current_liabilities", 200, "2019-12-31", 2019, **shared),
        cash=make_fact("cash", 50, "2019-12-31", 2019, **shared),
        short_term_debt=make_fact("short_term_debt", 20, "2019-12-31", 2019, **shared),
        shares_outstanding=make_fact("shares_outstanding", 1000, "2019-12-31", 2019, **shared),
        restated_facts=[restated],
    )


def test_margin_of_safety_uses_restated_operating_income_once_public():
    year0 = make_statement(
        2018, "2018-12-31", operating_income=900, depreciation_amortization=90, capex=100,
        current_assets=400, current_liabilities=180, cash=40, short_term_debt=10,
    )
    year1 = _statement_with_restated_operating_income()
    assumptions = _assumptions()

    before = compute_margin_of_safety(
        [year0, year1], assumptions, market_price=50, as_of_date=date(2020, 6, 1)
    )
    after = compute_margin_of_safety(
        [year0, year1], assumptions, market_price=50, as_of_date=date(2020, 8, 1)
    )

    # FCFF = NOPAT + D&A - capex - change_in_NWC; only NOPAT differs between
    # the two runs (operating_income 1000 vs 800, tax_rate=0.25 from _assumptions()).
    assert before.dcf.base_fcff - after.dcf.base_fcff == pytest.approx((1000 - 800) * (1 - 0.25))
    assert before.margin_of_safety != after.margin_of_safety
