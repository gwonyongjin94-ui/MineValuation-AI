import pytest

from app.config import get_settings
from app.data.filing_documents import fetch_filing_document, list_recent_filings
from app.data.sec_client import SECClient
from app.data.ticker_map import build_default_ticker_map

pytestmark = pytest.mark.integration


def _latest_10k_text(ticker: str) -> str:
    settings = get_settings()
    tm = build_default_ticker_map()
    client = SECClient(user_agent=settings.sec_user_agent)
    try:
        cik = tm.resolve(ticker)
        submissions = client.get_submissions(cik)
        filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
        document = fetch_filing_document(client, cik, filings[0])
    finally:
        client.close()
    return document.text


def test_aapl_10k_cleans_to_readable_text_with_toc_anchors():
    text = _latest_10k_text("AAPL")

    assert "Risk Factors" in text
    assert "ix:nonNumeric" not in text
    assert 100_000 < len(text) < 2_000_000


def test_msft_10k_cleans_to_readable_text_without_toc_anchors():
    # MSFT's filer template has no hyperlinked table of contents (unlike
    # AAPL's) - confirms clean_filing_html() doesn't depend on that
    # structure, since it only strips markup rather than slicing sections.
    text = _latest_10k_text("MSFT")

    assert "Item 1A" in text
    assert "ix:nonNumeric" not in text
    assert 100_000 < len(text) < 5_000_000
