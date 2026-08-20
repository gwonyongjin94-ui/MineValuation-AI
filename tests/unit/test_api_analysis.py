import pytest
from fastapi.testclient import TestClient

from app.api.analysis import (
    get_anthropic_client,
    get_market_data_client,
    get_sec_client,
    get_sentiment_classifier,
    get_ticker_map,
)
from app.main import app
from tests.factories import (
    STANDARD_SUBMISSIONS,
    build_mock_market_data_client,
    build_mock_sec_client,
    build_ticker_map_with_cache,
    fake_anthropic_client,
    fake_anthropic_client_by_model,
    fake_sentiment_classifier,
    standard_company_facts,
)


@pytest.fixture
def client(tmp_path):
    app.dependency_overrides[get_sec_client] = lambda: build_mock_sec_client(
        STANDARD_SUBMISSIONS, standard_company_facts()
    )
    app.dependency_overrides[get_ticker_map] = lambda: build_ticker_map_with_cache(
        tmp_path, {"TSTX": 999999}
    )
    # Explicit None, not the real dependency - a real ANTHROPIC_API_KEY in the
    # local .env must never let a "not llm"-marked test make a real paid call.
    app.dependency_overrides[get_anthropic_client] = lambda: None
    # Mocked, not the real FRED client - "not llm"-marked tests must stay
    # fully offline, and compute_wacc defaults to False anyway so most
    # tests never touch this, but the override still needs to exist.
    app.dependency_overrides[get_market_data_client] = lambda: build_mock_market_data_client()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_analyze_endpoint_returns_valuation_with_default_assumptions(client):
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "TSTX", "market_price": 50.0, "as_of_date": "2026-01-01"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["company"]["ticker"] == "TSTX"
    assert body["margin_of_safety"] is not None
    assert body["unsupported_reason"] is None
    assert body["assumptions"]["discount_rate"] == 0.09
    assert len(body["financials"]) == 2
    assert len(body["sources"]) == 2
    assert body["qualitative_analyses"] == []

    # The latest (only computable) year uses market value of equity
    # (market_price=50.0 x shares_outstanding=1000 = 50,000), not book
    # equity (2,200) - see growth.py's module docstring for why. invested
    # capital = short_term_debt(30) + long_term_debt(320) + 50,000 - cash(80)
    # = 50,270; growth_rate = (net_capex + change_in_nwc) / invested_capital
    # = 90/50270 - the NOPAT term cancels between reinvestment_rate and
    # roic, so this is independent of the 0.21 default tax_rate (see
    # test_growth.py for the tax-rate-dependent reinvestment_rate/roic
    # breakdown).
    growth = body["fundamental_growth_estimate"]
    assert len(growth["by_year"]) == 2
    assert growth["suggested_growth_rate"] == pytest.approx(90 / 50270)
    assert growth["years_averaged"] == 1


def test_analyze_endpoint_accepts_assumption_overrides(client):
    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "TSTX",
            "market_price": 50.0,
            "assumptions": {"discount_rate": 0.12},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["assumptions"]["discount_rate"] == 0.12
    assert body["assumptions"]["tax_rate"] == 0.21


def test_analyze_endpoint_rejects_invalid_assumptions(client):
    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "TSTX",
            "market_price": 50.0,
            "assumptions": {"discount_rate": 0.02, "terminal_growth_rate": 0.05},
        },
    )

    assert response.status_code == 422


def test_analyze_endpoint_unknown_ticker_returns_404(tmp_path):
    app.dependency_overrides[get_sec_client] = lambda: build_mock_sec_client(
        STANDARD_SUBMISSIONS, standard_company_facts()
    )
    app.dependency_overrides[get_ticker_map] = lambda: build_ticker_map_with_cache(
        tmp_path, {"TSTX": 999999}
    )
    test_client = TestClient(app)

    response = test_client.post("/api/v1/analyze", json={"ticker": "NOPE", "market_price": 50.0})

    app.dependency_overrides.clear()
    assert response.status_code == 404


def test_analyze_endpoint_rejects_non_positive_market_price(client):
    response = client.post("/api/v1/analyze", json={"ticker": "TSTX", "market_price": 0})

    assert response.status_code == 422


def test_analyze_endpoint_returns_503_when_qualitative_requested_without_key(client):
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "TSTX", "market_price": 50.0, "analyze_10k": True},
    )

    assert response.status_code == 503


def test_analyze_endpoint_returns_qualitative_analysis_when_configured(client):
    app.dependency_overrides[get_anthropic_client] = lambda: fake_anthropic_client(
        risks=[{"label": "X", "description": "Y", "status": "emerging", "severity": "medium"}],
        summary="one notable risk",
    )

    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "TSTX", "market_price": 50.0, "analyze_10k": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["qualitative_analyses"]) == 1
    assert body["qualitative_analyses"][0]["source_label"] == "10-K"
    assert len(body["sources"]) == 3


def test_analyze_endpoint_earnings_call_text_produces_analysis(client):
    app.dependency_overrides[get_anthropic_client] = lambda: fake_anthropic_client(
        risks=[], summary="nothing notable"
    )

    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "TSTX",
            "market_price": 50.0,
            "earnings_call_text": "management discussed guidance",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["qualitative_analyses"]) == 1
    assert body["qualitative_analyses"][0]["source_label"] == "Earnings call (user-provided)"
    assert body["qualitative_analyses"][0]["source_accession_number"] is None


def test_analyze_endpoint_cross_validate_surfaces_partial_model_failure(client):
    low_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "low"}
    app.dependency_overrides[get_anthropic_client] = lambda: fake_anthropic_client_by_model(
        risks_by_model={"claude-haiku-4-5-20251001": [low_risk]},
        summary="minor risk",
        truncated_models=frozenset({"claude-sonnet-5"}),
    )

    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "TSTX",
            "market_price": 50.0,
            "analyze_10k": True,
            "cross_validate": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["qualitative_analyses"]) == 1
    assert any("cross-model validation" in w for w in body["warnings"])


def test_analyze_endpoint_uses_per_request_api_key_and_never_echoes_it(client, monkeypatch):
    # The server-configured client is forced to None by the `client` fixture,
    # so this only succeeds if the per-request key is what actually got used.
    captured = {}

    def fake_constructor(api_key):
        captured["api_key"] = api_key
        return fake_anthropic_client(risks=[], summary="fine")

    monkeypatch.setattr("app.api.analysis.anthropic.Anthropic", fake_constructor)

    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "TSTX",
            "market_price": 50.0,
            "analyze_10k": True,
            "anthropic_api_key": "sk-ant-user-supplied-secret",
        },
    )

    assert response.status_code == 200
    assert captured["api_key"] == "sk-ant-user-supplied-secret"
    assert "sk-ant-user-supplied-secret" not in response.text


def test_analyze_endpoint_returns_503_when_sentiment_unavailable(client, monkeypatch):
    monkeypatch.setattr("app.api.analysis.sentiment_is_available", lambda: False)

    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "TSTX", "market_price": 50.0, "include_sentiment": True},
    )

    assert response.status_code == 503


def test_analyze_endpoint_returns_sentiment_when_requested(client, monkeypatch):
    document_html = (
        "<html><body>"
        "<p>Competition has intensified across all our major product categories.</p>"
        "<p>Services revenue grew strongly across every geographic segment this year.</p>"
        "</body></html>"
    )
    app.dependency_overrides[get_sec_client] = lambda: build_mock_sec_client(
        STANDARD_SUBMISSIONS, standard_company_facts(), document_html=document_html
    )
    app.dependency_overrides[get_anthropic_client] = lambda: fake_anthropic_client(
        risks=[], summary="fine"
    )
    app.dependency_overrides[get_sentiment_classifier] = lambda: fake_sentiment_classifier(
        [("negative", 0.9), ("positive", 0.8)]
    )
    monkeypatch.setattr("app.api.analysis.sentiment_is_available", lambda: True)

    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "TSTX",
            "market_price": 50.0,
            "analyze_10k": True,
            "include_sentiment": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["sentiment_analyses"]) == 1
    assert body["sentiment_analyses"][0]["source_label"] == "10-K"
    assert body["sentiment_analyses"][0]["sentence_count"] == 2


def test_analyze_endpoint_returns_wacc_estimate_when_requested(client):
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "TSTX", "market_price": 50.0, "compute_wacc": True},
    )

    assert response.status_code == 200
    body = response.json()
    wacc = body["wacc_estimate"]
    assert wacc is not None
    assert wacc["industry"] == "Software (System & Application)"
    assert wacc["cost_of_equity"] is not None


def test_analyze_endpoint_wacc_estimate_omitted_by_default(client):
    response = client.post(
        "/api/v1/analyze", json={"ticker": "TSTX", "market_price": 50.0}
    )

    assert response.status_code == 200
    assert response.json()["wacc_estimate"] is None


def test_analyze_endpoint_returns_comps_estimate_when_requested(client):
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "TSTX", "market_price": 50.0, "compute_comps": True},
    )

    assert response.status_code == 200
    body = response.json()
    comps = body["comps_estimate"]
    assert comps is not None
    # STANDARD_SUBMISSIONS' SIC 3571 matches the "357" prefix bucket.
    assert comps["industry_sic_prefix"] == "357"
    # The test fixture's ticker_map only resolves TSTX, so every curated
    # peer (AAPL/DELL/HPQ/NTAP) fails to resolve and is skipped - a real
    # exercise of the per-peer resilience path, not a crash.
    assert comps["peers"] == []
    assert len(comps["warnings"]) > 0


def test_analyze_endpoint_comps_estimate_omitted_by_default(client):
    response = client.post(
        "/api/v1/analyze", json={"ticker": "TSTX", "market_price": 50.0}
    )

    assert response.status_code == 200
    assert response.json()["comps_estimate"] is None


def test_analyze_endpoint_returns_owner_earnings_and_consensus(client):
    response = client.post(
        "/api/v1/analyze", json={"ticker": "TSTX", "market_price": 50.0}
    )

    assert response.status_code == 200
    body = response.json()
    oe = body["owner_earnings_estimate"]
    assert oe is not None
    assert oe["value_per_share_low"] <= oe["value_per_share_high"]

    consensus = body["valuation_consensus"]
    methods = {r["method"] for r in consensus["ranges"]}
    assert methods == {"DCF (FCFF)", "DCF (Owner Earnings)"}
