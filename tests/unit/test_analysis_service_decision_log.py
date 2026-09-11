"""The decision log's two request-path flags, tested at the service
boundary: `log_decision` (writes a record) and `include_track_record`
(changes a prompt).

The load-bearing tests here are the isolation ones. This project's
standing rule is that reference figures never silently become inputs -
fundamental growth, WACC and comps are all reported alongside the
valuation rather than merged into it, with `use_wacc_as_discount_rate`
as the single explicit exception. A track record of past lessons is the
softest input yet (free text an LLM wrote about earlier mistakes), so
the rule matters most here: it may reach the qualitative prompt and
nothing else. Asserted directly rather than left to code review,
because a future refactor could route it into assumptions without any
test failing otherwise.
"""

from datetime import UTC, date, datetime

import pytest

from app.memory.decision_log import append_record, read_records
from app.memory.models import DecisionRecord, ReflectionRecord, Stance
from app.services.analysis_service import TRACK_RECORD_LESSON_LIMIT, analyze
from app.valuation.assumptions import ValuationAssumptions
from tests.factories import (
    BANK_SUBMISSIONS,
    STANDARD_SUBMISSIONS,
    bank_company_facts,
    build_mock_market_data_client,
    build_mock_sec_client,
    build_ticker_map_with_cache,
    fake_anthropic_client,
    sec_entry,
    standard_company_facts,
)

ASSUMPTIONS = ValuationAssumptions(
    fcff_growth_rate=0.05,
    discount_rate=0.10,
    terminal_growth_rate=0.03,
    tax_rate=0.25,
    forecast_years=3,
)


def run(tmp_path, log_path, *, submissions=None, facts=None, ticker="TSTX", **kwargs):
    return analyze(
        ticker=ticker,
        market_price=50.0,
        as_of_date=date(2026, 1, 1),
        assumptions=ASSUMPTIONS,
        client=build_mock_sec_client(
            submissions or STANDARD_SUBMISSIONS, facts or standard_company_facts()
        ),
        ticker_map=build_ticker_map_with_cache(tmp_path, {ticker: 999999}),
        decision_log_path=log_path,
        **kwargs,
    )


def seed_reflection(log_path, ticker="TSTX", lesson="past lesson", when=None):
    """A decision plus a reflection on it - the minimum the log needs
    before recent_lessons() will return anything for `ticker`.
    """
    decision = DecisionRecord(
        decision_id=f"seed-{lesson}",
        logged_at=datetime(2025, 1, 1, tzinfo=UTC),
        ticker=ticker,
        as_of_date=date(2025, 1, 1),
        market_price=40.0,
        valuation_category="standard",
        stance=Stance.OVERVALUED,
        assumptions=ASSUMPTIONS,
    )
    append_record(decision, log_path)
    append_record(
        ReflectionRecord(
            decision_id=decision.decision_id,
            created_at=when or datetime(2025, 4, 1, tzinfo=UTC),
            model="test-model",
            lesson=lesson,
        ),
        log_path,
    )


def test_nothing_is_written_unless_log_decision_is_set(tmp_path):
    log_path = tmp_path / "decisions.jsonl"

    run(tmp_path, log_path)

    assert not log_path.exists()


def test_log_decision_appends_a_record_matching_the_analysis(tmp_path):
    log_path = tmp_path / "decisions.jsonl"

    result = run(tmp_path, log_path, log_decision=True)

    records, warnings = read_records(log_path)
    assert warnings == []
    assert len(records) == 1
    record = records[0]
    assert record.ticker == "TSTX"
    assert record.as_of_date == date(2026, 1, 1)
    assert record.market_price == 50.0
    assert record.margin_of_safety == result.margin_of_safety.margin_of_safety
    assert record.assumptions == ASSUMPTIONS
    assert record.consensus_methods == [r.method for r in result.valuation_consensus.ranges]


def test_stance_follows_the_sign_of_the_margin_of_safety(tmp_path):
    log_path = tmp_path / "decisions.jsonl"

    result = run(tmp_path, log_path, log_decision=True)

    record = read_records(log_path)[0][0]
    expected = (
        Stance.UNDERVALUED
        if result.margin_of_safety.margin_of_safety > 0
        else Stance.OVERVALUED
    )
    assert record.stance == expected


def test_a_company_this_project_refuses_to_value_is_logged_as_unsupported(tmp_path):
    # Banks get no call at all - the log still records that an analysis
    # happened, so "we declined" is counted rather than invisible.
    log_path = tmp_path / "decisions.jsonl"

    run(
        tmp_path,
        log_path,
        submissions=BANK_SUBMISSIONS,
        facts=bank_company_facts(),
        ticker="TBNK",
        log_decision=True,
    )

    record = read_records(log_path)[0][0]
    assert record.stance == Stance.UNSUPPORTED
    assert record.margin_of_safety is None
    assert record.unsupported_reason


def test_logged_risks_are_counts_by_severity_with_no_labels_or_quotes(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    risks = [
        {
            "label": "Litigation over the Zeta patent",
            "description": "detail",
            "status": "emerging",
            "severity": "high",
            "supporting_quote": "we are a defendant in Zeta v. Test Co",
            "grounding": "explicit",
        },
        {
            "label": "FX exposure",
            "description": "detail",
            "status": "longstanding",
            "severity": "medium",
            "supporting_quote": "a strengthening dollar reduced revenue",
            "grounding": "explicit",
        },
    ]

    run(
        tmp_path,
        log_path,
        log_decision=True,
        earnings_call_text="confidential transcript mentioning Zeta",
        anthropic_client=fake_anthropic_client(risks=risks, summary="s"),
    )

    raw = log_path.read_text()
    record = read_records(log_path)[0][0]
    assert record.qualitative_risk_counts == {"high": 1, "medium": 1}
    # The privacy rule in app/memory/models.py, checked against the bytes
    # on disk rather than the parsed model.
    assert "Zeta" not in raw
    assert "confidential transcript" not in raw
    assert "FX exposure" not in raw


def test_appending_twice_builds_history_rather_than_overwriting(tmp_path):
    log_path = tmp_path / "decisions.jsonl"

    run(tmp_path, log_path, log_decision=True)
    run(tmp_path, log_path, log_decision=True)

    records = read_records(log_path)[0]
    assert len(records) == 2
    assert records[0].decision_id != records[1].decision_id


def test_a_failed_log_write_degrades_to_a_warning_and_keeps_the_result(tmp_path):
    # A disk failure must not cost the caller a result they already paid
    # SEC (and possibly LLM) calls for.
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("this is a file, so it can't be a parent directory")

    result = run(tmp_path, blocked / "decisions.jsonl", log_decision=True)

    assert result.margin_of_safety is not None
    assert any("could not append to the decision log" in w for w in result.warnings)


def test_track_record_reaches_the_qualitative_prompt(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    seed_reflection(log_path, lesson="be stricter about boilerplate risks")
    client = fake_anthropic_client(risks=[], summary="s")

    run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="transcript",
        anthropic_client=client,
    )

    prompt = client.messages.sent[0]["messages"][0]["content"]
    assert "be stricter about boilerplate risks" in prompt
    assert "NOT" in prompt


def test_track_record_never_changes_the_assumptions_or_the_valuation(tmp_path):
    # The invariant this whole feature is built around: a lesson is
    # allowed to change how an LLM weighs risk, and nothing else.
    log_path = tmp_path / "decisions.jsonl"
    seed_reflection(log_path, lesson="this system is far too bearish on large caps")

    baseline = run(
        tmp_path,
        log_path,
        earnings_call_text="transcript",
        anthropic_client=fake_anthropic_client(risks=[], summary="s"),
    )
    with_lessons = run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="transcript",
        anthropic_client=fake_anthropic_client(risks=[], summary="s"),
    )

    assert with_lessons.margin_of_safety.margin_of_safety == (
        baseline.margin_of_safety.margin_of_safety
    )
    assert with_lessons.margin_of_safety.intrinsic_value_per_share == (
        baseline.margin_of_safety.intrinsic_value_per_share
    )
    assert with_lessons.margin_of_safety.intrinsic_value_low == (
        baseline.margin_of_safety.intrinsic_value_low
    )
    assert with_lessons.margin_of_safety.intrinsic_value_high == (
        baseline.margin_of_safety.intrinsic_value_high
    )
    assert [r.method for r in with_lessons.valuation_consensus.ranges] == [
        r.method for r in baseline.valuation_consensus.ranges
    ]


def test_a_populated_log_does_not_change_prompts_unless_asked(tmp_path):
    # Opt-in means opt-in: an instance with years of history still sends
    # byte-for-byte the prompt it always sent until the flag is set.
    log_path = tmp_path / "decisions.jsonl"
    seed_reflection(log_path, lesson="a lesson that must not leak in")
    without = fake_anthropic_client(risks=[], summary="s")
    with_flag = fake_anthropic_client(risks=[], summary="s")

    run(tmp_path, log_path, earnings_call_text="t", anthropic_client=without)
    run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="t",
        anthropic_client=with_flag,
    )

    plain = without.messages.sent[0]["messages"][0]["content"]
    augmented = with_flag.messages.sent[0]["messages"][0]["content"]
    assert "a lesson that must not leak in" not in plain
    assert "a lesson that must not leak in" in augmented
    assert len(augmented) > len(plain)


def test_track_record_only_draws_on_this_ticker_s_own_history(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    seed_reflection(log_path, ticker="TSTX", lesson="lesson about TSTX")
    seed_reflection(log_path, ticker="OTHR", lesson="lesson about OTHR")
    client = fake_anthropic_client(risks=[], summary="s")

    run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="t",
        anthropic_client=client,
    )

    prompt = client.messages.sent[0]["messages"][0]["content"]
    assert "lesson about TSTX" in prompt
    assert "lesson about OTHR" not in prompt


def test_at_most_the_lesson_limit_is_injected(tmp_path):
    # These compete for attention with a 60-70k-token filing; the cap is
    # the point, so it is asserted rather than trusted.
    log_path = tmp_path / "decisions.jsonl"
    for i in range(TRACK_RECORD_LESSON_LIMIT + 3):
        seed_reflection(
            log_path,
            lesson=f"lesson number {i}",
            when=datetime(2025, 4, 1, tzinfo=UTC).replace(minute=i),
        )
    client = fake_anthropic_client(risks=[], summary="s")

    run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="t",
        anthropic_client=client,
    )

    prompt = client.messages.sent[0]["messages"][0]["content"]
    injected = sum(1 for i in range(TRACK_RECORD_LESSON_LIMIT + 3) if f"lesson number {i}" in prompt)
    assert injected == TRACK_RECORD_LESSON_LIMIT


def test_requesting_a_track_record_with_no_history_warns_instead_of_failing(tmp_path):
    log_path = tmp_path / "decisions.jsonl"
    client = fake_anthropic_client(risks=[], summary="s")

    result = run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="t",
        anthropic_client=client,
    )

    prompt = client.messages.sent[0]["messages"][0]["content"]
    assert "Lessons from this system" not in prompt
    assert any("no reflections" in w for w in result.warnings)


def test_using_a_track_record_says_so_in_the_warnings(tmp_path):
    # The caller has to be able to tell, from the result alone, that the
    # LLM saw something the filing didn't contain.
    log_path = tmp_path / "decisions.jsonl"
    seed_reflection(log_path, lesson="a lesson")

    result = run(
        tmp_path,
        log_path,
        include_track_record=True,
        earnings_call_text="t",
        anthropic_client=fake_anthropic_client(risks=[], summary="s"),
    )

    assert any(
        "decision log" in w and "never the computed valuation numbers" in w
        for w in result.warnings
    )


def test_the_record_stores_the_assumptions_that_ran_not_the_ones_requested(tmp_path):
    # use_wacc_as_discount_rate rebinds `assumptions` mid-analysis. A log
    # that recorded the requested 10% would describe a valuation that
    # never happened - and every later reflection would then be reasoning
    # about the wrong discount rate.
    log_path = tmp_path / "decisions.jsonl"
    facts = standard_company_facts()
    facts["facts"]["us-gaap"]["InterestExpense"] = {
        "units": {
            "USD": [
                sec_entry(50, "2023-12-31", 2023, filed="2024-02-01", accn="A-2023",
                          start="2023-01-01"),
                sec_entry(60, "2024-12-31", 2024, filed="2025-02-01", accn="A-2024",
                          start="2024-01-01"),
            ]
        }
    }

    result = run(
        tmp_path,
        log_path,
        facts=facts,
        log_decision=True,
        use_wacc_as_discount_rate=True,
        market_data_client=build_mock_market_data_client(4.50),
    )

    record = read_records(log_path)[0][0]
    assert result.wacc_estimate.wacc is not None
    assert record.assumptions.discount_rate == pytest.approx(result.wacc_estimate.wacc)
    assert record.assumptions.discount_rate != pytest.approx(ASSUMPTIONS.discount_rate)
    assert record.used_wacc_as_discount_rate is True
