import anthropic
import pytest

from app.config import get_settings
from app.data.filing_documents import fetch_filing_document, list_recent_filings
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map
from app.qualitative.risk_extraction import extract_risks

pytestmark = [pytest.mark.integration, pytest.mark.llm]


def test_extract_risks_real_aapl_filing():
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not configured")

    ticker_map = build_default_ticker_map()
    sec_client = build_default_client()
    cik = ticker_map.resolve("AAPL")
    submissions = sec_client.get_submissions(cik)
    filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
    document = fetch_filing_document(sec_client, cik, filings[0])
    sec_client.close()

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    result = extract_risks(client, document)

    assert 1 <= len(result.risks) <= 8
    assert result.summary
    assert result.input_tokens > 0
