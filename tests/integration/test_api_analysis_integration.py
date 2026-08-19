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
