"""LLM-based qualitative risk extraction from a block of company text.

Works on either an SEC filing's cleaned text or a user-pasted earnings
call transcript - same extraction logic either way, only the source
label and (for filings) the accession number differ. Earnings calls
aren't SEC data and V2 doesn't fetch them automatically (no free,
reliable source); the caller supplies the transcript text directly.

Section-level extraction (Risk Factors only, MD&A only) was tried and
rejected for filings - see docs/DATA_SPIKE_NOTES.md's V2 section. The
whole cleaned document is handed to the model instead; spiked against a
real AAPL 10-K (~48k input tokens on Haiku, a few cents) and the output
was grounded in specific filing details (the actual DMA fine amount,
Greater China sales figures) rather than generic summary - see
scripts/spike_qualitative_risk.py.

Uses forced tool-use rather than parsing free-text/markdown output: the
model always returns validated structured data instead of prose that
would need fragile regex parsing.

Two reliability techniques adapted from Caridi, Giovannini & Ricciardi
Celsi, "AI-Assisted Value Investing" (Electronics 2026, 15, 1155):
- Each risk must carry a verbatim supporting_quote and a grounding
  label (explicit vs inferred) - the paper's citation-or-label
  discipline, adapted from numeric extraction to qualitative claims.
  This is the G1-equivalent check for this layer: a human can spot-check
  a claim against its quote without re-reading the whole document.
- run_cross_model_extraction() runs the same prompt across two Claude
  tiers and flags disagreement, adapted from the paper's cross-model
  (GPT-4 vs Claude) protocol - within one vendor rather than across
  vendors, since this project already has an Anthropic key and adding
  a second provider is a separate credential decision, not a free
  substitution.
"""

from enum import Enum

from pydantic import BaseModel

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_RISKS = 8
# 2000 was enough before supporting_quote/grounding were added to the
# schema, but Sonnet hit max_tokens mid-response with the larger schema on
# a real AAPL 10-K (confirmed live, not assumed) - stop_reason=="max_tokens"
# left tool_use.input == {}, which failed downstream with a confusing
# KeyError rather than a clear error. Bumped up, and the truncation case is
# now checked explicitly below instead of silently producing empty output.
MAX_TOKENS = 4096
CROSS_VALIDATION_MODELS = ("claude-haiku-4-5-20251001", "claude-sonnet-5")
# Disagreement is a coarse, honest signal - not semantic risk-matching
# across the two runs (unreliable without another LLM call to do the
# matching, which just relocates the trust problem). If the two models'
# high-severity counts differ by this much or more, it's surfaced as a
# warning for the analyst to look at both lists directly.
DISAGREEMENT_THRESHOLD = 2

_PROMPT = """You are analyzing the following {source_label} content for a company.

Extract the most material, company-specific qualitative risks - not generic industry
boilerplate that would apply to almost any company in the sector. Use ONLY the text
provided below - do not draw on prior or general knowledge about this company. Ground
every risk in what the text actually says. List at most {max_risks} risks, ordered by
materiality, then give a 2-3 sentence overall summary.

For each risk, also provide:
- supporting_quote: a short verbatim excerpt (one sentence or less) copied directly from
  the text that supports this risk. If you cannot find a passage that directly supports
  a candidate risk, do not include that risk at all.
- grounding: "explicit" if the text directly states this as a risk, or "inferred" if you
  are drawing a reasonable conclusion that goes beyond what is literally stated.

Text follows:
---
{text}
---
"""

_TOOL = {
    "name": "record_qualitative_risks",
    "description": "Record the material, company-specific qualitative risks found in the text.",
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
                        "supporting_quote": {"type": "string"},
                        "grounding": {"type": "string", "enum": ["explicit", "inferred"]},
                    },
                    "required": [
                        "label",
                        "description",
                        "status",
                        "severity",
                        "supporting_quote",
                        "grounding",
                    ],
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


class RiskGrounding(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"


class QualitativeRisk(BaseModel):
    label: str
    description: str
    status: RiskStatus
    severity: RiskSeverity
    supporting_quote: str
    grounding: RiskGrounding


class QualitativeRiskAnalysis(BaseModel):
    source_label: str
    source_accession_number: str | None = None
    model: str
    risks: list[QualitativeRisk]
    summary: str
    input_tokens: int
    output_tokens: int


class CrossModelRiskAnalysis(BaseModel):
    analyses: list[QualitativeRiskAnalysis]
    high_severity_count_range: tuple[int, int]
    disagreement: bool
    failed_models: list[str] = []


class QualitativeAnalysisError(Exception):
    pass


def extract_risks(
    client,
    text: str,
    source_label: str,
    source_accession_number: str | None = None,
    model: str = DEFAULT_MODEL,
) -> QualitativeRiskAnalysis:
    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "record_qualitative_risks"},
        messages=[
            {
                "role": "user",
                "content": _PROMPT.format(
                    source_label=source_label, max_risks=MAX_RISKS, text=text
                ),
            }
        ],
    )

    if response.stop_reason == "max_tokens":
        raise QualitativeAnalysisError(
            f"response truncated at max_tokens={MAX_TOKENS} before completing the tool call "
            f"(model={model})"
        )

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        raise QualitativeAnalysisError("model did not return the expected tool call")

    # Defensive, not paranoid: reproduced live against a real AAPL 10-K on
    # claude-sonnet-5 - stop_reason=="tool_use" (not truncated) but the
    # model emitted 6,456 "risks" entries against a maxItems=8 schema
    # constraint and never got to the "summary" field. The JSON schema's
    # maxItems is a hint to the model, not a server-enforced hard limit, so
    # a degenerate response like this is possible even without truncation.
    risks_data = tool_use.input.get("risks")
    summary = tool_use.input.get("summary")
    if risks_data is None or summary is None:
        raise QualitativeAnalysisError(
            f"model returned an incomplete tool call (model={model}, "
            f"keys={list(tool_use.input.keys())})"
        )
    if len(risks_data) > MAX_RISKS:
        raise QualitativeAnalysisError(
            f"model returned {len(risks_data)} risks against a maximum of {MAX_RISKS} - "
            f"likely a degenerate response (model={model})"
        )

    return QualitativeRiskAnalysis(
        source_label=source_label,
        source_accession_number=source_accession_number,
        model=model,
        risks=risks_data,
        summary=summary,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


def _high_severity_count(analysis: QualitativeRiskAnalysis) -> int:
    return sum(1 for risk in analysis.risks if risk.severity == RiskSeverity.HIGH)


def run_cross_model_extraction(
    client,
    text: str,
    source_label: str,
    source_accession_number: str | None = None,
    models: tuple[str, ...] = CROSS_VALIDATION_MODELS,
) -> CrossModelRiskAnalysis:
    # One model failing (rate limit, degenerate output, truncation) doesn't
    # invalidate the other's result - a partial cross-check is still useful,
    # and losing the whole analysis because of one flaky call would be worse
    # than surfacing the failure alongside whatever did succeed.
    analyses: list[QualitativeRiskAnalysis] = []
    failed_models: list[str] = []
    for model in models:
        try:
            analyses.append(
                extract_risks(client, text, source_label, source_accession_number, model=model)
            )
        except QualitativeAnalysisError as exc:
            failed_models.append(f"{model}: {exc}")

    if not analyses:
        raise QualitativeAnalysisError(
            f"all models failed during cross-model extraction: {'; '.join(failed_models)}"
        )

    counts = [_high_severity_count(a) for a in analyses]
    count_range = (min(counts), max(counts))
    disagreement = bool(failed_models) or (count_range[1] - count_range[0]) >= (
        DISAGREEMENT_THRESHOLD
    )

    return CrossModelRiskAnalysis(
        analyses=analyses,
        high_severity_count_range=count_range,
        disagreement=disagreement,
        failed_models=failed_models,
    )
