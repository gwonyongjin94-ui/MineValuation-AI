from types import SimpleNamespace

import pytest

from app.data.filing_documents import FilingDocument
from app.qualitative.risk_extraction import QualitativeAnalysisError, extract_risks

FILING = FilingDocument(
    cik="0000320193",
    form="10-K",
    accession_number="A-1",
    filed_date="2025-10-31",
    document_url="https://example.com/doc.htm",
    text="some filing text",
)


def _fake_client(tool_input=None, no_tool_use=False):
    content = []
    if not no_tool_use:
        content.append(SimpleNamespace(type="tool_use", input=tool_input))
    response = SimpleNamespace(
        content=content, usage=SimpleNamespace(input_tokens=100, output_tokens=50)
    )

    class FakeMessages:
        def create(self, **kwargs):
            return response

    return SimpleNamespace(messages=FakeMessages())


def test_extract_risks_parses_tool_use_response():
    tool_input = {
        "risks": [
            {"label": "X", "description": "Y", "status": "emerging", "severity": "high"},
        ],
        "summary": "overall summary",
    }
    client = _fake_client(tool_input=tool_input)

    result = extract_risks(client, FILING)

    assert result.risks[0].label == "X"
    assert result.risks[0].status.value == "emerging"
    assert result.risks[0].severity.value == "high"
    assert result.summary == "overall summary"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.source_accession_number == "A-1"


def test_extract_risks_raises_when_no_tool_use_returned():
    client = _fake_client(no_tool_use=True)

    with pytest.raises(QualitativeAnalysisError):
        extract_risks(client, FILING)
