"""Score past decisions against what the price actually did, then write
LLM reflections on the gap.

    python scripts/resolve_decisions.py              # resolve + reflect
    python scripts/resolve_decisions.py --no-reflect # free: no LLM calls
    python scripts/resolve_decisions.py --horizon 180

Deliberately a separate script, not part of /api/v1/analyze: resolving
outcomes needs price history (network) and reflection needs an LLM
(money), and this project's rule is that a normal analysis request never
silently incurs either. Run it on a schedule, or whenever you want the
log caught up.

Both phases are idempotent - a decision that already has an outcome is
skipped, and so is one that already has a reflection - so re-running
after a partial failure only does the work that's still missing.
"""

import argparse
import sys
from datetime import date

import anthropic

from app.config import get_settings
from app.data.market_data import (
    MarketDataError,
    build_default_market_data_client,
    fetch_price_history,
)
from app.memory.decision_log import (
    append_record,
    pending_reflection,
    pending_resolution,
    read_records,
)
from app.memory.reflection import DEFAULT_HORIZON_DAYS, resolve_outcome, write_reflection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--horizon", type=int, default=DEFAULT_HORIZON_DAYS,
        help=f"days after a decision to score it against (default: {DEFAULT_HORIZON_DAYS})",
    )
    parser.add_argument(
        "--no-reflect", action="store_true",
        help="resolve outcomes only, skip the LLM reflection step (no API cost)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="process at most this many decisions in each phase",
    )
    args = parser.parse_args()

    log_path = get_settings().resolved_decision_log_path
    records, read_warnings = read_records(log_path)
    for warning in read_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if not records:
        print(f"decision log is empty ({log_path}) - run an analysis with log_decision first")
        return 0

    today = date.today()  # noqa: DTZ011 - calendar date default is fine
    due = pending_resolution(records, args.horizon, today)
    if args.limit:
        due = due[: args.limit]
    print(f"{len(due)} decision(s) ready to resolve at a {args.horizon}-day horizon")

    # One price history per ticker, not per decision - several decisions
    # on the same company are the normal case once this log has any age.
    market_data_client = build_default_market_data_client()
    history_by_ticker: dict[str, list] = {}
    resolved_count = 0
    try:
        for decision in due:
            if decision.ticker not in history_by_ticker:
                try:
                    history_by_ticker[decision.ticker] = fetch_price_history(
                        decision.ticker, market_data_client
                    )
                except MarketDataError as exc:
                    print(f"  {decision.ticker}: price history unavailable ({exc})")
                    history_by_ticker[decision.ticker] = []

            history = history_by_ticker[decision.ticker]
            outcome = resolve_outcome(decision, history, args.horizon) if history else None
            if outcome is None:
                print(f"  {decision.ticker} {decision.as_of_date}: not resolvable yet, skipped")
                continue

            append_record(outcome, log_path)
            resolved_count += 1
            verdict = {True: "right", False: "wrong", None: "no call"}[
                outcome.stance_was_directionally_right
            ]
            print(
                f"  {decision.ticker} {decision.as_of_date}: {decision.stance.value} -> "
                f"{outcome.return_pct:+.1%} ({verdict})"
            )
    finally:
        market_data_client.close()
    print(f"resolved {resolved_count} outcome(s)")

    if args.no_reflect:
        print("--no-reflect set - skipping the LLM reflection phase")
        return 0

    api_key = get_settings().anthropic_api_key
    if not api_key:
        print(
            "error: reflection needs ANTHROPIC_API_KEY in .env (or use --no-reflect)",
            file=sys.stderr,
        )
        return 1

    # Re-read so outcomes just appended above are visible to this phase.
    records, _ = read_records(log_path)
    pairs = pending_reflection(records)
    if args.limit:
        pairs = pairs[: args.limit]
    print(f"\n{len(pairs)} decision(s) awaiting reflection (real Anthropic API cost)")

    anthropic_client = anthropic.Anthropic(api_key=api_key)
    for decision, outcome in pairs:
        try:
            reflection = write_reflection(anthropic_client, decision, outcome)
        except anthropic.AnthropicError as exc:
            print(f"  {decision.ticker} {decision.as_of_date}: reflection failed ({exc})")
            continue
        append_record(reflection, log_path)
        print(f"  {decision.ticker} {decision.as_of_date}: {reflection.lesson}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
