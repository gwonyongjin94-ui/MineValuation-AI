from datetime import date

import pytest

from app.data.exceptions import UnknownTickerError
from app.qualitative.risk_extraction import QualitativeAnalysisError
from app.services.analysis_service import analyze
from app.valuation.assumptions import ValuationAssumptions
from tests.factories import (
    BANK_SUBMISSIONS,
    STANDARD_SUBMISSIONS,
    bank_company_facts,
    build_mock_sec_client,
    build_ticker_map_with_cache,
    fake_anthropic_client,
    fake_anthropic_client_by_model,
    fake_sentiment_classifier,
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

    # FY2023 is the first year (no prior-year NWC), so only FY2024 is
    # computable: reinvestment_rate=(180-120+30)/900=0.1. FY2024 is also
    # the latest year, so ROIC uses market value of equity
    # (market_price=50.0 x shares_outstanding=1000=50,000), not book
    # equity (2,200) - see growth.py's module docstring for why:
    # roic=900/(30+320+50000-80)=900/50270
    growth = result.fundamental_growth_estimate
    assert len(growth.by_year) == 2
    assert growth.by_year[0].growth_rate is None
    assert growth.by_year[1].reinvestment_rate == pytest.approx(0.1)
    assert growth.by_year[1].roic == pytest.approx(900 / 50270)
    assert growth.suggested_growth_rate == pytest.approx(0.1 * (900 / 50270))
    assert growth.years_averaged == 1
    assert any("market value of equity" in w for w in growth.by_year[1].warnings)


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


def test_analyze_10k_adds_qualitative_analysis_and_source(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        analyze_10k=True,
        anthropic_client=fake_anthropic_client(
            risks=[{"label": "X", "description": "Y", "status": "emerging", "severity": "low"}],
            summary="minor risks only",
        ),
    )

    assert len(result.qualitative_analyses) == 1
    assert result.qualitative_analyses[0].source_label == "10-K"
    assert len(result.sources) == 3


def test_analyze_earnings_call_text_produces_separate_analysis(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        earnings_call_text="management discussed headwinds in the call",
        anthropic_client=fake_anthropic_client(risks=[], summary="no major new risks"),
    )

    assert len(result.qualitative_analyses) == 1
    assert result.qualitative_analyses[0].source_label == "Earnings call (user-provided)"
    assert result.qualitative_analyses[0].source_accession_number is None


def test_analyze_raises_when_qualitative_requested_without_client(tmp_path):
    with pytest.raises(QualitativeAnalysisError):
        analyze(
            ticker="TSTX",
            market_price=50.0,
            as_of_date=date(2026, 1, 1),
            assumptions=_assumptions(),
            client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
            ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
            analyze_10k=True,
        )


def test_analyze_warns_on_multiple_high_severity_risks(tmp_path):
    high_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "high"}
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        analyze_10k=True,
        anthropic_client=fake_anthropic_client(
            risks=[high_risk, high_risk], summary="two severe risks"
        ),
    )

    assert any("high-severity qualitative risk" in w for w in result.warnings)


def test_analyze_10k_include_sentiment_adds_summary(tmp_path):
    document_html = (
        "<html><body>"
        "<p>Competition has intensified across all our major product categories.</p>"
        "<p>Services revenue grew strongly across every geographic segment this year.</p>"
        "</body></html>"
    )
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(
            STANDARD_SUBMISSIONS, standard_company_facts(), document_html=document_html
        ),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        analyze_10k=True,
        anthropic_client=fake_anthropic_client(risks=[], summary="fine"),
        include_sentiment=True,
        sentiment_classifier=fake_sentiment_classifier(
            [("negative", 0.9), ("positive", 0.8)]
        ),
    )

    assert len(result.sentiment_analyses) == 1
    assert result.sentiment_analyses[0].source_label == "10-K"
    assert result.sentiment_analyses[0].sentence_count == 2


def test_analyze_10k_cross_validate_reports_no_disagreement_when_models_agree(tmp_path):
    low_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "low"}
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        analyze_10k=True,
        anthropic_client=fake_anthropic_client_by_model(
            risks_by_model={
                "claude-haiku-4-5-20251001": [low_risk],
                "claude-sonnet-5": [low_risk],
            },
            summary="minor risk",
        ),
        cross_validate=True,
    )

    assert len(result.qualitative_analyses) == 2
    assert not any("cross-model" in w for w in result.warnings)


def test_analyze_10k_cross_validate_warns_when_a_model_fails(tmp_path):
    low_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "low"}
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        analyze_10k=True,
        anthropic_client=fake_anthropic_client_by_model(
            risks_by_model={"claude-haiku-4-5-20251001": [low_risk]},
            summary="minor risk",
            truncated_models=frozenset({"claude-sonnet-5"}),
        ),
        cross_validate=True,
    )

    assert len(result.qualitative_analyses) == 1
    assert any(
        "cross-model validation" in w and "failed" in w for w in result.warnings
    )


def test_analyze_without_include_sentiment_leaves_it_empty(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        analyze_10k=True,
        anthropic_client=fake_anthropic_client(risks=[], summary="fine"),
    )

    assert result.sentiment_analyses == []
