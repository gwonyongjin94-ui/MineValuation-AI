"""Fetch and clean raw 10-K/10-Q filing documents (not XBRL facts).

There's no SEC API that returns just "the Risk Factors section" - filer-
generated HTML structure varies too much between vendors to reliably
slice out one Item. Checked live: AAPL's table of contents links each
Item to an anchor id, so "content between the Item 1A and Item 1B
anchors" cleanly isolates Risk Factors. MSFT's table of contents has no
such links at all - "Item 1A" appears as plain repeated text with
nothing to anchor a slice to. Building a per-vendor heuristic parser
was rejected in favor of handing the LLM the whole cleaned document and
letting it locate what it needs - see docs/DATA_SPIKE_NOTES.md's V2
section for the two-company comparison this is based on.
"""

import re
from datetime import date

from bs4 import BeautifulSoup
from pydantic import BaseModel

from app.data.sec_client import SECClient

FILING_FORMS = ("10-K", "10-Q")

_HIDDEN_STYLE = re.compile(r"display:\s*none")


class FilingSummary(BaseModel):
    form: str
    accession_number: str
    filed_date: date
    primary_document: str


class FilingDocument(BaseModel):
    cik: str
    form: str
    accession_number: str
    filed_date: date
    document_url: str
    text: str


def list_recent_filings(
    submissions: dict, forms: tuple[str, ...] = FILING_FORMS
) -> list[FilingSummary]:
    recent = submissions["filings"]["recent"]
    return [
        FilingSummary(
            form=form,
            accession_number=recent["accessionNumber"][i],
            filed_date=recent["filingDate"][i],
            primary_document=recent["primaryDocument"][i],
        )
        for i, form in enumerate(recent["form"])
        if form in forms
    ]


def build_document_url(cik: str, accession_number: str, primary_document: str) -> str:
    cik_no_leading_zeros = str(int(cik))
    accession_no_dashes = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_no_leading_zeros}/"
        f"{accession_no_dashes}/{primary_document}"
    )


def clean_filing_html(html: str) -> str:
    """Strip tags/scripts/styles and the hidden inline-XBRL metadata block.

    Inline XBRL documents carry a `display:none` block (the `ix:header`
    tag soup with every XBRL fact restated as hidden text) that BeautifulSoup's
    get_text() would otherwise include verbatim - it doesn't respect CSS.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(style=_HIDDEN_STYLE):
        tag.decompose()

    text = soup.get_text(separator=" ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_filing_document(
    client: SECClient, cik: str, filing: FilingSummary
) -> FilingDocument:
    url = build_document_url(cik, filing.accession_number, filing.primary_document)
    html = client.get_document(url)
    return FilingDocument(
        cik=cik,
        form=filing.form,
        accession_number=filing.accession_number,
        filed_date=filing.filed_date,
        document_url=url,
        text=clean_filing_html(html),
    )
