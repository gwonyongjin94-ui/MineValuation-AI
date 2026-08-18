import anthropic
import pytest

from app.config import get_settings
from app.data.filing_documents import fetch_filing_document, list_recent_filings
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map
from app.qualitative.risk_extraction import extract_risks, run_cross_model_extraction

pytestmark = [pytest.mark.integration, pytest.mark.llm]


def _real_aapl_10k_text():
    ticker_map = build_default_ticker_map()
    sec_client = build_default_client()
    cik = ticker_map.resolve("AAPL")
    submissions = sec_client.get_submissions(cik)
    filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
    document = fetch_filing_document(sec_client, cik, filings[0])
    sec_client.close()
    return document


def test_extract_risks_real_aapl_filing():
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not configured")

    document = _real_aapl_10k_text()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    result = extract_risks(
        client, document.text, document.form, source_accession_number=document.accession_number
    )

    assert 1 <= len(result.risks) <= 8
    assert result.summary
    assert result.input_tokens > 0
    for risk in result.risks:
        assert risk.supporting_quote
        assert risk.grounding is not None


def test_run_cross_model_extraction_real_aapl_filing():
    # NOTE: on this real ~70k-token AAPL 10-K, claude-sonnet-5 reproducibly
    # (2/2 runs observed) returns a degenerate tool call - thousands of
    # "risks" entries against the maxItems=8 schema hint, never reaching
    # "summary" - while claude-haiku-4-5 completes cleanly every time. This
    # isn't asserted as "both models succeed"; it asserts what actually
    # happens: run_cross_model_extraction() must not crash when that occurs,
    # and must surface the failure rather than silently dropping it. See
    # docs/DATA_SPIKE_NOTES.md's V2 section for the full writeup.
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not configured")

    document = _real_aapl_10k_text()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    result = run_cross_model_extraction(
        client, document.text, document.form, source_accession_number=document.accession_number
    )

    assert 1 <= len(result.analyses) <= 2
    if len(result.analyses) < 2:
        assert result.failed_models
        assert result.disagreement is True
    print(
        f"\nhigh-severity range: {result.high_severity_count_range}, "
        f"disagreement={result.disagreement}, failed_models={result.failed_models}"
    )
    for analysis in result.analyses:
        print(f"{analysis.model}: {len(analysis.risks)} risks, summary={analysis.summary[:100]}")
