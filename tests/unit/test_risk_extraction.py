import pytest

from app.qualitative.risk_extraction import QualitativeAnalysisError, extract_risks
from tests.factories import fake_anthropic_client


def test_extract_risks_parses_tool_use_response_for_filing():
    risks = [{"label": "X", "description": "Y", "status": "emerging", "severity": "high"}]
    client = fake_anthropic_client(risks=risks, summary="overall summary")

    result = extract_risks(client, "some filing text", "10-K", source_accession_number="A-1")

    assert result.risks[0].label == "X"
    assert result.risks[0].status.value == "emerging"
    assert result.risks[0].severity.value == "high"
    assert result.summary == "overall summary"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.source_label == "10-K"
    assert result.source_accession_number == "A-1"


def test_extract_risks_works_for_user_pasted_earnings_call_text():
    client = fake_anthropic_client(risks=[], summary="no material risks flagged")

    result = extract_risks(client, "transcript text here", "Q3 2025 earnings call")

    assert result.source_label == "Q3 2025 earnings call"
    assert result.source_accession_number is None


def test_extract_risks_raises_when_no_tool_use_returned():
    client = fake_anthropic_client(no_tool_use=True)

    with pytest.raises(QualitativeAnalysisError):
        extract_risks(client, "text", "10-K")
