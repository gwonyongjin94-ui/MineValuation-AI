import pytest
from fastapi.testclient import TestClient

from app.api.analysis import get_sec_client, get_ticker_map
from app.main import app
from tests.factories import (
    STANDARD_SUBMISSIONS,
    build_mock_sec_client,
    build_ticker_map_with_cache,
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
