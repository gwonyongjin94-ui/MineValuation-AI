from datetime import date

import pytest

from app.data.exceptions import UnknownTickerError
from app.services.analysis_service import analyze
from app.valuation.assumptions import ValuationAssumptions
from tests.factories import (
    BANK_SUBMISSIONS,
    STANDARD_SUBMISSIONS,
    bank_company_facts,
    build_mock_sec_client,
    build_ticker_map_with_cache,
    standard_company_facts,
)


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


def test_analyze_end_to_end_with_mocked_sec(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
    )

    assert result.company.ticker == "TSTX"
    assert result.company.valuation_category.value == "standard"
    assert len(result.financials) == 2
    assert len(result.metrics) == 2
    assert result.unsupported_reason is None
    assert result.margin_of_safety is not None
    assert result.margin_of_safety.intrinsic_value_per_share is not None
    assert len(result.sources) == 2


def test_analyze_financial_company_has_no_margin_of_safety(tmp_path):
    result = analyze(
        ticker="TBNK",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(BANK_SUBMISSIONS, bank_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TBNK": 888888}),
    )

    assert result.company.valuation_category.value == "financial"
    assert result.margin_of_safety is None
    assert result.unsupported_reason is not None
    assert "financial" in result.unsupported_reason


def test_analyze_unknown_ticker_raises(tmp_path):
    with pytest.raises(UnknownTickerError):
        analyze(
            ticker="NOPE",
            market_price=50.0,
            as_of_date=date(2026, 1, 1),
            assumptions=_assumptions(),
            client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
            ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        )
