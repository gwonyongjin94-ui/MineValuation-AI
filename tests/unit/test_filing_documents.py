import httpx

from app.data.filing_documents import (
    FilingSummary,
    build_document_url,
    clean_filing_html,
    fetch_filing_document,
    list_recent_filings,
)
from app.data.sec_client import SECClient

SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-K", "8-K", "10-Q", "DEF 14A", "10-Q"],
            "accessionNumber": ["A-1", "A-2", "A-3", "A-4", "A-5"],
            "filingDate": ["2025-11-01", "2025-08-01", "2025-05-01", "2025-03-01", "2025-02-01"],
            "primaryDocument": ["a1.htm", "a2.htm", "a3.htm", "a4.htm", "a5.htm"],
        }
    }
}


def test_list_recent_filings_filters_to_10k_and_10q():
    filings = list_recent_filings(SUBMISSIONS)

    assert [f.accession_number for f in filings] == ["A-1", "A-3", "A-5"]
    assert all(f.form in ("10-K", "10-Q") for f in filings)


def test_build_document_url_strips_leading_zeros_and_dashes():
    url = build_document_url("0000320193", "0000320193-25-000079", "aapl-20250927.htm")

    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/aapl-20250927.htm"
    )


def test_clean_filing_html_strips_script_style_and_hidden_ixbrl_block():
    html = """
    <html><body>
    <div style="display:none"><ix:header>garbage metadata should not appear</ix:header></div>
    <script>var x = 1;</script>
    <style>.foo { color: red; }</style>
    <p>Item 1A. Risk Factors</p>
    <p>The Company faces competition.</p>
    </body></html>
    """

    text = clean_filing_html(html)

    assert "garbage metadata" not in text
    assert "var x = 1" not in text
    assert "color: red" not in text
    assert "Item 1A. Risk Factors" in text
    assert "The Company faces competition." in text


def test_fetch_filing_document_builds_url_and_cleans_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "320193" in str(request.url)
        return httpx.Response(200, text="<html><body><p>hello filing</p></body></html>")

    client = SECClient(
        user_agent="test", min_interval=0, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    filing = FilingSummary(
        form="10-K", accession_number="0000320193-25-000079", filed_date="2025-10-31",
        primary_document="aapl-20250927.htm",
    )

    document = fetch_filing_document(client, "0000320193", filing)

    assert document.text == "hello filing"
    assert document.accession_number == "0000320193-25-000079"
    assert "320193" in document.document_url
