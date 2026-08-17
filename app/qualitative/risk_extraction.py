"""LLM-based qualitative risk extraction from a cleaned filing document.

Section-level extraction (Risk Factors only, MD&A only) was tried and
rejected - see docs/DATA_SPIKE_NOTES.md's V2 section. The whole cleaned
document is handed to the model instead; spiked against a real AAPL
10-K (~48k input tokens on Haiku, a few cents) and the output was
grounded in specific filing details (the actual DMA fine amount,
Greater China sales figures) rather than generic summary - see
scripts/spike_qualitative_risk.py.

Uses forced tool-use rather than parsing free-text/markdown output: the
model always returns validated structured data instead of prose that
would need fragile regex parsing.
"""

from enum import Enum

from pydantic import BaseModel

from app.data.filing_documents import FilingDocument

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_RISKS = 8

_PROMPT = """You are analyzing the Risk Factors and MD&A content of a company's {form} filing.

Extract the most material, company-specific qualitative risks - not generic industry
boilerplate that would apply to almost any company in the sector. Ground every risk in
what the filing text actually says, not general knowledge about the company. List at
most {max_risks} risks, ordered by materiality, then give a 2-3 sentence overall summary.

Filing text follows:
---
{text}
---
"""

_TOOL = {
    "name": "record_qualitative_risks",
    "description": "Record the material, company-specific qualitative risks found in a filing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "risks": {
                "type": "array",
                "maxItems": MAX_RISKS,
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string", "enum": ["emerging", "longstanding"]},
                        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": ["label", "description", "status", "severity"],
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["risks", "summary"],
    },
}


class RiskStatus(str, Enum):
    EMERGING = "emerging"
    LONGSTANDING = "longstanding"


class RiskSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QualitativeRisk(BaseModel):
    label: str
    description: str
    status: RiskStatus
    severity: RiskSeverity


class QualitativeRiskAnalysis(BaseModel):
    source_form: str
    source_accession_number: str
    model: str
    risks: list[QualitativeRisk]
    summary: str
    input_tokens: int
    output_tokens: int


class QualitativeAnalysisError(Exception):
    pass


def extract_risks(
    client, filing: FilingDocument, model: str = DEFAULT_MODEL
) -> QualitativeRiskAnalysis:
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_qualitative_risks"},
        messages=[
            {
                "role": "user",
                "content": _PROMPT.format(
                    form=filing.form, max_risks=MAX_RISKS, text=filing.text
                ),
            }
        ],
    )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise QualitativeAnalysisError("model did not return the expected tool call")

    return QualitativeRiskAnalysis(
        source_form=filing.form,
        source_accession_number=filing.accession_number,
        model=model,
        risks=tool_use.input["risks"],
        summary=tool_use.input["summary"],
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
