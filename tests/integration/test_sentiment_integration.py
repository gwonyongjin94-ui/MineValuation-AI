import pytest

transformers = pytest.importorskip("transformers", reason="optional [sentiment] extra not installed")

from app.data.filing_documents import fetch_filing_document, list_recent_filings
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map
from app.qualitative.sentiment import score_sentiment

pytestmark = [pytest.mark.integration, pytest.mark.model]


def test_score_sentiment_real_aapl_filing():
    ticker_map = build_default_ticker_map()
    sec_client = build_default_client()
    cik = ticker_map.resolve("AAPL")
    submissions = sec_client.get_submissions(cik)
    filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
    document = fetch_filing_document(sec_client, cik, filings[0])
    sec_client.close()

    result = score_sentiment(document.text, "10-K")

    assert result.sentence_count > 100
    assert result.negative_count + result.positive_count + result.neutral_count == (
        result.sentence_count
    )
    assert 0.0 <= result.negative_ratio <= 1.0
    assert len(result.most_negative) <= 5
