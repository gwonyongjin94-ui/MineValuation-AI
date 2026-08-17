import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = [pytest.mark.integration, pytest.mark.llm]

client = TestClient(app)


def test_analyze_aapl_with_10k_qualitative_analysis_real():
    response = client.post(
        "/api/v1/analyze",
        json={"ticker": "AAPL", "market_price": 230.0, "analyze_10k": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert 1 <= len(body["qualitative_analyses"]) == 1
    analysis = body["qualitative_analyses"][0]
    assert analysis["source_label"] == "10-K"
    assert analysis["source_accession_number"]
    assert 1 <= len(analysis["risks"]) <= 8
    assert len(body["sources"]) == 3


def test_analyze_earnings_call_text_real():
    response = client.post(
        "/api/v1/analyze",
        json={
            "ticker": "AAPL",
            "market_price": 230.0,
            "earnings_call_text": (
                "Management noted continued softness in Greater China and highlighted "
                "ongoing regulatory scrutiny in the EU as a near-term headwind, while "
                "expressing confidence in services growth."
            ),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["qualitative_analyses"]) == 1
    assert body["qualitative_analyses"][0]["source_label"] == "Earnings call (user-provided)"
