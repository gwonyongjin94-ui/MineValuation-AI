"""Append-only JSONL storage for the decision log.

A pure leaf module: it depends on app/valuation/assumptions.py (through
the record schema) and nothing else in this app. In particular it does
NOT import app/services - the service layer builds a DecisionRecord and
hands it here, never the reverse, so this stays importable from a
standalone script (scripts/resolve_decisions.py) with no FastAPI or SEC
client in the picture.

Every write is an append of one JSON line. Nothing is ever rewritten in
place, so a half-finished write can only ever corrupt the last line -
read_records() skips unparseable lines with a warning instead of
failing the whole read, which keeps one bad line from making the entire
history unreadable.
"""

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path

from app.memory.models import (
    DecisionRecord,
    OutcomeRecord,
    RecordKind,
    ReflectionRecord,
)

_KIND_TO_MODEL = {
    RecordKind.DECISION: DecisionRecord,
    RecordKind.OUTCOME: OutcomeRecord,
    RecordKind.REFLECTION: ReflectionRecord,
}

Record = DecisionRecord | OutcomeRecord | ReflectionRecord


def append_record(record: Record, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(record.model_dump_json() + "\n")


def read_records(path: Path) -> tuple[list[Record], list[str]]:
    """All records in file order, plus a warning per line that couldn't be
    parsed. A missing file is an empty log, not an error - the first run
    of anything reading this has nothing to read yet.
    """
    if not path.exists():
        return [], []

    records: list[Record] = []
    warnings: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                model = _KIND_TO_MODEL[RecordKind(raw["kind"])]
                records.append(model.model_validate(raw))
            except (ValueError, KeyError, TypeError) as exc:
                warnings.append(f"{path.name}:{line_number} skipped unparseable record ({exc})")
    return records, warnings


def decisions(records: Iterable[Record]) -> list[DecisionRecord]:
    return [r for r in records if isinstance(r, DecisionRecord)]


def resolved_decision_ids(records: Iterable[Record]) -> set[str]:
    return {r.decision_id for r in records if isinstance(r, OutcomeRecord)}


def reflected_decision_ids(records: Iterable[Record]) -> set[str]:
    return {r.decision_id for r in records if isinstance(r, ReflectionRecord)}


def pending_resolution(
    records: Iterable[Record], horizon_days: int, today: date
) -> list[DecisionRecord]:
    """Decisions old enough to have a `horizon_days` outcome, that don't
    have one logged yet.

    Ordered oldest-first so a run that hits an API limit partway through
    still makes forward progress on the longest-waiting decisions.
    """
    records = list(records)
    already_resolved = resolved_decision_ids(records)
    due = [
        d
        for d in decisions(records)
        if d.decision_id not in already_resolved
        and (today - d.as_of_date).days >= horizon_days
    ]
    return sorted(due, key=lambda d: d.as_of_date)


def pending_reflection(records: Iterable[Record]) -> list[tuple[DecisionRecord, OutcomeRecord]]:
    """(decision, outcome) pairs that have a resolved outcome but no
    reflection written yet - the input queue for the LLM step.
    """
    records = list(records)
    outcome_by_id = {r.decision_id: r for r in records if isinstance(r, OutcomeRecord)}
    already_reflected = reflected_decision_ids(records)
    pairs = [
        (d, outcome_by_id[d.decision_id])
        for d in decisions(records)
        if d.decision_id in outcome_by_id and d.decision_id not in already_reflected
    ]
    return sorted(pairs, key=lambda pair: pair[0].as_of_date)


def recent_lessons(
    records: Iterable[Record], ticker: str | None = None, limit: int = 5
) -> list[str]:
    """Most recent reflection lessons, newest first.

    `ticker` filters to one company's history; None returns lessons
    across every ticker, which is what a calibration question ("do we
    systematically over-flag risk?") actually needs. Joining back to the
    decision is what makes the ticker filter possible at all -
    ReflectionRecord itself only carries a decision_id.
    """
    records = list(records)
    ticker_by_id = {d.decision_id: d.ticker for d in decisions(records)}
    reflections = [r for r in records if isinstance(r, ReflectionRecord)]
    if ticker is not None:
        reflections = [
            r for r in reflections if ticker_by_id.get(r.decision_id) == ticker.upper()
        ]
    reflections.sort(key=lambda r: r.created_at, reverse=True)
    return [r.lesson for r in reflections[:limit]]


def format_track_record(lessons: list[str]) -> str:
    """Renders lessons as the prompt block injected into qualitative
    analysis (see app/qualitative/risk_extraction.py).

    Deliberately plain text with an explicit framing line: these are
    this system's own past mistakes, not facts about the company, and
    the prompt has to say so or the model will treat them as evidence
    about the filing it's reading.
    """
    numbered = "\n".join(f"{i}. {lesson}" for i, lesson in enumerate(lessons, start=1))
    return (
        "Lessons from this system's own past analyses, written after "
        "comparing earlier conclusions against what the stock actually did. "
        "These describe this analyst's historical calibration - they are NOT "
        "facts about the company below, and must not be cited as evidence "
        "from its filings:\n"
        f"{numbered}"
    )


def now_utc() -> datetime:
    return datetime.now(tz=UTC)
