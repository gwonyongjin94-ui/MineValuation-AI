import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.integration

client = TestClient(app)


def test_analyze_aapl_real_sec_data():
    response = client.post(
        "/api/v1/analyze", json={"ticker": "AAPL", "market_price": 230.0}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["company"]["ticker"] == "AAPL"
    assert body["company"]["valuation_category"] == "standard"
    assert len(body["financials"]) > 1
    assert body["margin_of_safety"] is not None
    assert body["margin_of_safety"]["intrinsic_value_per_share"] is not None
    assert len(body["sources"]) == 2
    growth = body["fundamental_growth_estimate"]
    assert len(growth["by_year"]) > 1
    assert growth["suggested_growth_rate"] is not None


def test_analyze_jpm_real_sec_data_is_unsupported():
    response = client.post("/api/v1/analyze", json={"ticker": "JPM", "market_price": 200.0})

    assert response.status_code == 200
    body = response.json()
    assert body["company"]["valuation_category"] == "financial"
    assert body["margin_of_safety"] is None
    assert body["unsupported_reason"] is not None
    # Confirms DATA_SPIKE_NOTES.md finding #6 (no capex/operating-income
    # tags for a bank) also means no computable fundamental growth rate -
    # this degrades to warnings rather than crashing.
    assert body["fundamental_growth_estimate"]["suggested_growth_rate"] is None


def test_analyze_unknown_ticker_returns_404_real():
    response = client.post(
        "/api/v1/analyze", json={"ticker": "NOTATICKER123", "market_price": 100.0}
    )

    assert response.status_code == 404


def test_analyze_compute_wacc_real_data():
    # Hits real SEC EDGAR and real FRED (10-year Treasury yield) - both
    # free, no API key, same tier of "always-on in CI" as the other
    # real-SEC tests in this file.
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "HD", "market_price": 344.3, "compute_wacc": True},
    )

    assert response.status_code == 200
    body = response.json()
    wacc = body["wacc_estimate"]
    assert wacc is not None
    assert wacc["industry"] == "Retail (Building Supply)"
    assert 0 < wacc["risk_free_rate"] < 0.15
    assert wacc["cost_of_equity"] is not None
    # assumptions.discount_rate is never overwritten by the estimate.
    assert body["assumptions"]["discount_rate"] == 0.09


def test_analyze_compute_comps_real_data():
    # Hits real SEC EDGAR (target + curated peers) and real Yahoo Finance
    # (peer prices) - same "always-on in CI" tier as the other real-data
    # tests in this file.
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "NVDA", "market_price": 217.56, "compute_comps": True},
    )

    assert response.status_code == 200
    body = response.json()
    comps = body["comps_estimate"]
    assert comps is not None
    assert comps["industry_sic_prefix"] == "3674"
    assert len(comps["peers"]) > 1
    assert comps["median_ev_to_ebitda"] is not None
