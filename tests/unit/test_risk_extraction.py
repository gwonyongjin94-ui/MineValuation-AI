from types import SimpleNamespace

import pytest

from app.qualitative.risk_extraction import (
    CROSS_VALIDATION_MODELS,
    QualitativeAnalysisError,
    extract_risks,
    run_cross_model_extraction,
)
from tests.factories import fake_anthropic_client, fake_anthropic_client_by_model


def test_extract_risks_parses_tool_use_response_for_filing():
    risks = [
        {
            "label": "X",
            "description": "Y",
            "status": "emerging",
            "severity": "high",
            "supporting_quote": "the filing states X directly",
            "grounding": "explicit",
        }
    ]
    client = fake_anthropic_client(risks=risks, summary="overall summary")

    result = extract_risks(client, "some filing text", "10-K", source_accession_number="A-1")

    assert result.risks[0].label == "X"
    assert result.risks[0].status.value == "emerging"
    assert result.risks[0].severity.value == "high"
    assert result.risks[0].supporting_quote == "the filing states X directly"
    assert result.risks[0].grounding.value == "explicit"
    assert result.summary == "overall summary"
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.source_label == "10-K"
    assert result.source_accession_number == "A-1"


def test_extract_risks_defaults_grounding_fields_when_test_omits_them():
    # fake_anthropic_client fills supporting_quote/grounding with defaults
    # when a test only cares about severity/status - confirms the schema
    # still requires them (would raise a pydantic ValidationError otherwise).
    risks = [{"label": "X", "description": "Y", "status": "emerging", "severity": "high"}]
    client = fake_anthropic_client(risks=risks)

    result = extract_risks(client, "text", "10-K")

    assert result.risks[0].supporting_quote
    assert result.risks[0].grounding.value in ("explicit", "inferred")


def test_extract_risks_works_for_user_pasted_earnings_call_text():
    client = fake_anthropic_client(risks=[], summary="no material risks flagged")

    result = extract_risks(client, "transcript text here", "Q3 2025 earnings call")

    assert result.source_label == "Q3 2025 earnings call"
    assert result.source_accession_number is None


def test_extract_risks_raises_when_no_tool_use_returned():
    client = fake_anthropic_client(no_tool_use=True)

    with pytest.raises(QualitativeAnalysisError):
        extract_risks(client, "text", "10-K")


def test_extract_risks_raises_clear_error_when_response_truncated():
    # Reproduces a real failure: claude-sonnet-5 hit max_tokens mid-tool-call
    # on a real AAPL 10-K once supporting_quote/grounding were added to the
    # schema, leaving tool_use.input == {} - this used to surface as a
    # confusing KeyError deep in pydantic validation instead of a clear error.
    client = fake_anthropic_client(truncated=True)

    with pytest.raises(QualitativeAnalysisError, match="truncated"):
        extract_risks(client, "text", "10-K")


def test_extract_risks_raises_clear_error_on_degenerate_oversized_response():
    # Reproduced live: claude-sonnet-5 returned 6,456 "risks" entries against
    # a maxItems=8 schema hint (not server-enforced) and never emitted
    # "summary" - not a truncation (stop_reason was "tool_use"), just a
    # degenerate generation. Constructed here via a raw fake response since
    # fake_anthropic_client's risk-filling helper isn't meant to build
    # thousands of entries.
    huge_risks = [
        {
            "label": f"X{i}",
            "description": "Y",
            "status": "emerging",
            "severity": "low",
            "supporting_quote": "q",
            "grounding": "explicit",
        }
        for i in range(20)
    ]
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use", input={"risks": huge_risks, "summary": "degenerate summary"}
            )
        ],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        stop_reason="tool_use",
    )

    class FakeMessages:
        def create(self, **kwargs):
            return response

    client = SimpleNamespace(messages=FakeMessages())

    with pytest.raises(QualitativeAnalysisError, match="degenerate"):
        extract_risks(client, "text", "10-K")


def test_run_cross_model_extraction_survives_one_model_failing():
    haiku, sonnet = CROSS_VALIDATION_MODELS
    high_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "high"}
    client = fake_anthropic_client_by_model(
        {haiku: [high_risk]}, truncated_models=frozenset({sonnet})
    )

    result = run_cross_model_extraction(client, "text", "10-K")

    assert len(result.analyses) == 1
    assert result.analyses[0].model == haiku
    assert len(result.failed_models) == 1
    assert sonnet in result.failed_models[0]
    assert result.disagreement is True


def test_run_cross_model_extraction_raises_when_every_model_fails():
    haiku, sonnet = CROSS_VALIDATION_MODELS
    client = fake_anthropic_client_by_model({}, truncated_models=frozenset({haiku, sonnet}))

    with pytest.raises(QualitativeAnalysisError, match="all models failed"):
        run_cross_model_extraction(client, "text", "10-K")


def test_run_cross_model_extraction_no_disagreement_for_similar_severity_counts():
    haiku, sonnet = CROSS_VALIDATION_MODELS
    high_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "high"}
    client = fake_anthropic_client_by_model(
        {haiku: [high_risk], sonnet: [high_risk, dict(high_risk, label="X2")]}
    )

    result = run_cross_model_extraction(client, "text", "10-K")

    assert len(result.analyses) == 2
    assert {a.model for a in result.analyses} == set(CROSS_VALIDATION_MODELS)
    assert result.high_severity_count_range == (1, 2)
    assert result.disagreement is False


def test_run_cross_model_extraction_flags_disagreement_on_diverging_severity_counts():
    haiku, sonnet = CROSS_VALIDATION_MODELS
    high_risk = {"label": "X", "description": "Y", "status": "emerging", "severity": "high"}
    client = fake_anthropic_client_by_model(
        {
            haiku: [],
            sonnet: [dict(high_risk, label=f"X{i}") for i in range(3)],
        }
    )

    result = run_cross_model_extraction(client, "text", "10-K")

    assert result.high_severity_count_range == (0, 3)
    assert result.disagreement is True
