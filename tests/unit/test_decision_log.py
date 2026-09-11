from datetime import UTC, date, datetime

from app.memory.decision_log import (
    append_record,
    decisions,
    format_track_record,
    pending_reflection,
    pending_resolution,
    read_records,
    recent_lessons,
    reflected_decision_ids,
    resolved_decision_ids,
)
from app.memory.models import (
    DecisionRecord,
    OutcomeRecord,
    ReflectionRecord,
    Stance,
)
from app.valuation.assumptions import ValuationAssumptions

ASSUMPTIONS = ValuationAssumptions(
    fcff_growth_rate=0.05,
    discount_rate=0.09,
    terminal_growth_rate=0.025,
    tax_rate=0.21,
)


def make_decision(
    decision_id: str = "d1",
    ticker: str = "TST",
    as_of_date: date = date(2025, 1, 15),
    stance: Stance = Stance.OVERVALUED,
    margin_of_safety: float | None = -0.4,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        logged_at=datetime(2025, 1, 15, 12, 0, tzinfo=UTC),
        ticker=ticker,
        as_of_date=as_of_date,
        market_price=100.0,
        valuation_category="standard",
        stance=stance,
        assumptions=ASSUMPTIONS,
        margin_of_safety=margin_of_safety,
    )


def make_outcome(decision_id: str = "d1", return_pct: float = 0.1) -> OutcomeRecord:
    return OutcomeRecord(
        decision_id=decision_id,
        resolved_at=datetime(2025, 4, 15, 12, 0, tzinfo=UTC),
        horizon_days=90,
        price_at_decision=100.0,
        price_at_horizon=100.0 * (1 + return_pct),
        price_date_at_horizon=date(2025, 4, 15),
        return_pct=return_pct,
        stance_was_directionally_right=False,
    )


def make_reflection(
    decision_id: str = "d1",
    lesson: str = "a lesson",
    created_at: datetime = datetime(2025, 4, 15, 13, 0, tzinfo=UTC),
) -> ReflectionRecord:
    return ReflectionRecord(
        decision_id=decision_id,
        created_at=created_at,
        model="test-model",
        lesson=lesson,
    )


def test_append_and_read_round_trips_all_three_record_kinds(tmp_path):
    path = tmp_path / "log.jsonl"
    append_record(make_decision(), path)
    append_record(make_outcome(), path)
    append_record(make_reflection(), path)

    records, warnings = read_records(path)

    assert warnings == []
    assert [type(r) for r in records] == [DecisionRecord, OutcomeRecord, ReflectionRecord]
    assert records[0].assumptions.discount_rate == 0.09
    assert records[1].return_pct == 0.1
    assert records[2].lesson == "a lesson"


def test_append_creates_missing_parent_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "log.jsonl"

    append_record(make_decision(), path)

    assert path.exists()
    assert len(read_records(path)[0]) == 1


def test_reading_a_missing_file_is_an_empty_log_not_an_error(tmp_path):
    records, warnings = read_records(tmp_path / "never-written.jsonl")

    assert records == []
    assert warnings == []


def test_a_corrupt_last_line_does_not_make_the_history_unreadable(tmp_path):
    # The failure mode append-only is designed around: a half-finished
    # write can only ever damage the final line.
    path = tmp_path / "log.jsonl"
    append_record(make_decision(decision_id="d1"), path)
    append_record(make_decision(decision_id="d2"), path)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "decision", "decision_id": "d3", "tick')

    records, warnings = read_records(path)

    assert [r.decision_id for r in records] == ["d1", "d2"]
    assert len(warnings) == 1
    assert "log.jsonl:3" in warnings[0]


def test_unknown_record_kind_is_skipped_with_a_warning(tmp_path):
    path = tmp_path / "log.jsonl"
    append_record(make_decision(), path)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"kind": "something_new", "decision_id": "d2"}\n')

    records, warnings = read_records(path)

    assert len(records) == 1
    assert len(warnings) == 1


def test_blank_lines_are_ignored_without_warning(tmp_path):
    path = tmp_path / "log.jsonl"
    append_record(make_decision(), path)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n\n")

    records, warnings = read_records(path)

    assert len(records) == 1
    assert warnings == []


def test_decisions_and_id_helpers_partition_the_stream():
    records = [make_decision(), make_outcome(), make_reflection()]

    assert [d.decision_id for d in decisions(records)] == ["d1"]
    assert resolved_decision_ids(records) == {"d1"}
    assert reflected_decision_ids(records) == {"d1"}


def test_pending_resolution_needs_the_full_horizon_to_have_elapsed():
    decision = make_decision(as_of_date=date(2025, 1, 1))

    # One day short of the horizon, then exactly at it.
    assert pending_resolution([decision], 90, date(2025, 3, 31)) == []
    assert pending_resolution([decision], 90, date(2025, 4, 1)) == [decision]


def test_pending_resolution_skips_decisions_that_already_have_an_outcome():
    records = [
        make_decision(decision_id="d1", as_of_date=date(2025, 1, 1)),
        make_decision(decision_id="d2", as_of_date=date(2025, 1, 2)),
        make_outcome(decision_id="d1"),
    ]

    due = pending_resolution(records, 90, date(2025, 12, 31))

    assert [d.decision_id for d in due] == ["d2"]


def test_pending_resolution_returns_oldest_first():
    records = [
        make_decision(decision_id="new", as_of_date=date(2025, 6, 1)),
        make_decision(decision_id="old", as_of_date=date(2025, 1, 1)),
    ]

    due = pending_resolution(records, 90, date(2025, 12, 31))

    assert [d.decision_id for d in due] == ["old", "new"]


def test_pending_reflection_pairs_only_resolved_and_unreflected_decisions():
    records = [
        make_decision(decision_id="unresolved", as_of_date=date(2025, 1, 1)),
        make_decision(decision_id="resolved", as_of_date=date(2025, 1, 2)),
        make_decision(decision_id="done", as_of_date=date(2025, 1, 3)),
        make_outcome(decision_id="resolved"),
        make_outcome(decision_id="done"),
        make_reflection(decision_id="done"),
    ]

    pairs = pending_reflection(records)

    assert [d.decision_id for d, _ in pairs] == ["resolved"]
    assert pairs[0][1].decision_id == "resolved"


def test_recent_lessons_are_newest_first_and_respect_the_limit():
    records = [
        make_decision(decision_id="d1"),
        make_reflection(
            decision_id="d1", lesson="older", created_at=datetime(2025, 1, 1, tzinfo=UTC)
        ),
        make_reflection(
            decision_id="d1", lesson="newer", created_at=datetime(2025, 6, 1, tzinfo=UTC)
        ),
    ]

    assert recent_lessons(records) == ["newer", "older"]
    assert recent_lessons(records, limit=1) == ["newer"]


def test_recent_lessons_filters_by_ticker_through_the_decision_join():
    # ReflectionRecord carries only a decision_id, so filtering by ticker
    # is only possible by joining back to the decision it came from.
    records = [
        make_decision(decision_id="a", ticker="AAPL"),
        make_decision(decision_id="m", ticker="MSFT"),
        make_reflection(decision_id="a", lesson="apple lesson"),
        make_reflection(decision_id="m", lesson="microsoft lesson"),
    ]

    assert recent_lessons(records, ticker="aapl") == ["apple lesson"]
    assert sorted(recent_lessons(records)) == ["apple lesson", "microsoft lesson"]


def test_recent_lessons_is_empty_when_the_ticker_has_no_reflections():
    records = [make_decision(decision_id="a", ticker="AAPL"), make_reflection(decision_id="a")]

    assert recent_lessons(records, ticker="MSFT") == []


def test_format_track_record_frames_lessons_as_calibration_not_evidence():
    block = format_track_record(["first lesson", "second lesson"])

    assert "1. first lesson" in block
    assert "2. second lesson" in block
    # The framing is the whole point - without it the model reads these
    # as facts about the filing it is being handed.
    assert "NOT" in block
    assert "facts about the company" in block
