from datetime import date

import httpx
import pytest

from app.data.exceptions import UnknownTickerError
from app.data.market_data import MarketDataError
from app.qualitative.risk_extraction import QualitativeAnalysisError
from app.services.analysis_service import analyze
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.wacc import FALLBACK_RISK_FREE_RATE
from tests.factories import (
    BANK_SUBMISSIONS,
    STANDARD_SUBMISSIONS,
    bank_company_facts,
    build_mock_market_data_client,
    build_mock_sec_client,
    build_ticker_map_with_cache,
    fake_anthropic_client,
    fake_anthropic_client_by_model,
    fake_sentiment_classifier,
    sec_entry,
    sec_facts,
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


def test_analyze_without_compute_wacc_leaves_wacc_estimate_none(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
    )

    assert result.wacc_estimate is None


def test_analyze_compute_wacc_produces_estimate(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        compute_wacc=True,
        market_data_client=build_mock_market_data_client(4.50),
    )

    wacc = result.wacc_estimate
    assert wacc is not None
    assert wacc.risk_free_rate == pytest.approx(0.045)
    # STANDARD_SUBMISSIONS' SIC 3571 matches the "357" prefix bucket.
    assert wacc.industry == "Software (System & Application)"
    assert wacc.cost_of_equity is not None
    # standard_company_facts() has no InterestExpense tag, so cost of debt
    # (and therefore a full WACC) can't be computed - degrades with a
    # warning rather than guessing, same pattern as every other missing-tag
    # case in this project.
    assert wacc.cost_of_debt_aftertax is None
    assert wacc.wacc is None
    assert any("cannot estimate cost of debt" in w for w in wacc.warnings)


def test_analyze_compute_wacc_without_client_raises(tmp_path):
    with pytest.raises(MarketDataError):
        analyze(
            ticker="TSTX",
            market_price=50.0,
            as_of_date=date(2026, 1, 1),
            assumptions=_assumptions(),
            client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
            ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
            compute_wacc=True,
        )


def test_analyze_compute_wacc_falls_back_to_constant_rate_when_fetch_fails(tmp_path):
    # FRED's live fetch has a confirmed real failure mode (reproducibly
    # blocked from GitHub Actions' IP range - see wacc.py's module
    # docstring) - the feature must not go dark over one unreachable data
    # point, so this asserts the fallback constant keeps wacc_estimate
    # usable, with a warning naming the degradation.
    def erroring_handler(request):
        raise httpx.ConnectError("boom", request=request)

    broken_market_data_client = httpx.Client(transport=httpx.MockTransport(erroring_handler))

    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        compute_wacc=True,
        market_data_client=broken_market_data_client,
    )

    assert result.wacc_estimate is not None
    assert result.wacc_estimate.risk_free_rate == FALLBACK_RISK_FREE_RATE
    assert any("risk-free rate unavailable" in w for w in result.warnings)


def test_analyze_without_compute_comps_leaves_comps_estimate_none(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
    )

    assert result.comps_estimate is None


def test_analyze_compute_comps_produces_estimate_with_unresolvable_peers(tmp_path):
    # STANDARD_SUBMISSIONS' SIC 3571 matches comps.py's "357" bucket
    # (AAPL/DELL/HPQ/NTAP), none of which this test's ticker_map can
    # resolve - every peer is skipped, not a crash, and that's exactly
    # what's asserted here (the full peer-computation path is covered by
    # tests/unit/test_comps.py).
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
        compute_comps=True,
        market_data_client=build_mock_market_data_client(4.50),
    )

    comps = result.comps_estimate
    assert comps is not None
    assert comps.industry_sic_prefix == "357"
    assert comps.peers == []


def test_analyze_compute_comps_without_client_raises(tmp_path):
    with pytest.raises(MarketDataError):
        analyze(
            ticker="TSTX",
            market_price=50.0,
            as_of_date=date(2026, 1, 1),
            assumptions=_assumptions(),
            client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
            ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
            compute_comps=True,
        )


def test_analyze_always_computes_owner_earnings_estimate(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
    )

    oe = result.owner_earnings_estimate
    assert oe is not None
    assert oe.value_per_share_low is not None
    assert oe.value_per_share_high is not None
    assert oe.value_per_share_low <= oe.value_per_share_high


def test_analyze_owner_earnings_estimate_none_for_financial_company(tmp_path):
    result = analyze(
        ticker="TBNK",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(BANK_SUBMISSIONS, bank_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TBNK": 888888}),
    )

    assert result.owner_earnings_estimate is None
    assert any("Owner Earnings DCF unavailable" in w for w in result.warnings)


def test_analyze_valuation_consensus_combines_dcf_and_owner_earnings_ranges(tmp_path):
    result = analyze(
        ticker="TSTX",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, standard_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TSTX": 999999}),
    )

    consensus = result.valuation_consensus
    methods = {r.method for r in consensus.ranges}
    # comps wasn't requested, so only the two DCF variants contribute.
    assert methods == {"DCF (FCFF)", "DCF (Owner Earnings)"}
    assert consensus.overlap_low is not None or "no overlap" in " ".join(consensus.warnings)


def test_analyze_valuation_consensus_empty_for_financial_company(tmp_path):
    result = analyze(
        ticker="TBNK",
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(BANK_SUBMISSIONS, bank_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"TBNK": 888888}),
    )

    assert result.valuation_consensus.ranges == []
    assert any("no valuation method produced a range" in w for w in result.valuation_consensus.warnings)


def _ifrs_company_facts() -> dict:
    # Small, internally-consistent IFRS/20-F fixture (not NVO's real
    # magnitudes - test_normalizer.py's test_normalize_ifrs_filer_
    # reproduces_nvo_case already covers the real-scale numbers). Two
    # fiscal years, like standard_company_facts(): FCFF for the first
    # year is always None (no prior year to diff NWC against - see
    # fcff.py), so margin_of_safety needs a second year to be computable
    # at all.
    def _dur(tag_2024, tag_2025):
        return [
            sec_entry(tag_2024, "2024-12-31", 2024, form="20-F", filed="2025-02-01",
                      accn="NVOX-2024", start="2024-01-01"),
            sec_entry(tag_2025, "2025-12-31", 2025, form="20-F", filed="2026-02-04",
                      accn="NVOX-2025", start="2025-01-01"),
        ]

    def _inst(val_2024, val_2025):
        return [
            sec_entry(val_2024, "2024-12-31", 2024, form="20-F", filed="2025-02-01",
                      accn="NVOX-2024"),
            sec_entry(val_2025, "2025-12-31", 2025, form="20-F", filed="2026-02-04",
                      accn="NVOX-2025"),
        ]

    monetary_tags = {
        "Revenue": _dur(900000, 1000000),
        "ProfitLossFromOperatingActivities": _dur(180000, 200000),
        "ProfitLoss": _dur(130000, 150000),
        "CashFlowsFromUsedInOperatingActivities": _dur(200000, 220000),
        "DepreciationAndAmortisationExpense": _dur(28000, 30000),
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": _dur(35000, 40000),
        "CurrentAssets": _inst(280000, 300000),
        "CurrentLiabilities": _inst(170000, 180000),
        "CashAndCashEquivalents": _inst(45000, 50000),
        "ShorttermBorrowings": _inst(18000, 20000),
        "LongtermBorrowings": _inst(85000, 90000),
        "Equity": _inst(230000, 250000),
    }
    share_tags = {"NumberOfSharesOutstanding": _inst(10000, 10000)}
    return {
        "cik": 1,
        "entityName": "IFRS Test Co",
        "facts": {
            "ifrs-full": {
                **sec_facts(monetary_tags, taxonomy="ifrs-full", unit="DKK")["facts"]["ifrs-full"],
                **sec_facts(share_tags, taxonomy="ifrs-full", unit="shares")["facts"]["ifrs-full"],
            }
        },
    }


def _fx_client(rate: float) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"chart": {"result": [{"meta": {"regularMarketPrice": rate}}]}}
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_analyze_ifrs_filer_raises_without_market_data_client(tmp_path):
    with pytest.raises(MarketDataError):
        analyze(
            ticker="NVOX",
            market_price=48.69,
            as_of_date=date(2026, 1, 1),
            assumptions=_assumptions(),
            client=build_mock_sec_client(STANDARD_SUBMISSIONS, _ifrs_company_facts()),
            ticker_map=build_ticker_map_with_cache(tmp_path, {"NVOX": 777777}),
        )


def test_analyze_ifrs_filer_converts_currency_and_computes_valuation(tmp_path):
    result = analyze(
        ticker="NVOX",
        market_price=48.69,
        # After both fixture statements' filed dates (2026-02-04) - an
        # earlier as_of_date look-ahead-excludes the FY2025 statement
        # (the only one with a computable FCFF; FY2024 is the first
        # year, so it has no prior year to diff NWC against - see
        # fcff.py), leaving nothing for margin_of_safety to compute.
        as_of_date=date(2026, 3, 1),
        assumptions=_assumptions(),
        client=build_mock_sec_client(STANDARD_SUBMISSIONS, _ifrs_company_facts()),
        ticker_map=build_ticker_map_with_cache(tmp_path, {"NVOX": 777777}),
        market_data_client=_fx_client(0.156),
    )

    assert result.margin_of_safety is not None
    latest = max(result.financials, key=lambda s: s.period_end)
    assert latest.revenue.unit == "USD"
    assert latest.revenue.value == pytest.approx(1000000 * 0.156)
    # Currency-agnostic - untouched by the conversion.
    assert latest.shares_outstanding.value == 10000
    assert any("converted from DKK to USD" in w for w in result.warnings)
