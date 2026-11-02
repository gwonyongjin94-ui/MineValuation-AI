from datetime import date
from types import SimpleNamespace

import pytest

from app.data.market_data import PricePoint
from app.memory.models import Stance
from app.memory.reflection import history_range_for, resolve_outcome, write_reflection
from tests.unit.test_decision_log import make_decision, make_outcome


def history(*pairs: tuple[str, float]) -> list[PricePoint]:
    return [PricePoint(date=date.fromisoformat(d), close=c) for d, c in pairs]


def fake_llm(text: str = "a generalizable lesson"):
    """Stands in for anthropic.Anthropic, matching only what
    write_reflection() touches.
    """
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=300, output_tokens=40),
    )

    class FakeMessages:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return response

    return SimpleNamespace(messages=FakeMessages())


def test_resolve_outcome_scores_an_overvalued_call_that_went_up_as_wrong():
    decision = make_decision(as_of_date=date(2025, 1, 1), stance=Stance.OVERVALUED)
    prices = history(("2025-01-02", 100.0), ("2025-04-02", 120.0))

    outcome = resolve_outcome(decision, prices, horizon_days=90)

    assert outcome is not None
    assert outcome.price_at_decision == 100.0
    assert outcome.price_at_horizon == 120.0
    assert outcome.return_pct == 0.2
    assert outcome.stance_was_directionally_right is False
    assert outcome.decision_id == decision.decision_id


def test_resolve_outcome_scores_an_overvalued_call_that_fell_as_right():
    decision = make_decision(as_of_date=date(2025, 1, 1), stance=Stance.OVERVALUED)
    prices = history(("2025-01-02", 100.0), ("2025-04-02", 80.0))

    outcome = resolve_outcome(decision, prices, horizon_days=90)

    assert outcome.return_pct == -0.2
    assert outcome.stance_was_directionally_right is True


def test_resolve_outcome_scores_an_undervalued_call_in_the_opposite_direction():
    decision = make_decision(as_of_date=date(2025, 1, 1), stance=Stance.UNDERVALUED)
    prices = history(("2025-01-02", 100.0), ("2025-04-02", 120.0))

    assert resolve_outcome(decision, prices, 90).stance_was_directionally_right is True

    falling = history(("2025-01-02", 100.0), ("2025-04-02", 80.0))
    assert resolve_outcome(decision, falling, 90).stance_was_directionally_right is False


def test_an_unsupported_decision_is_resolved_but_never_scored_right_or_wrong():
    # Banks and companies with missing FCFF inputs get no call at all;
    # scoring them either way would pollute the calibration statistics
    # the lessons are drawn from.
    decision = make_decision(
        as_of_date=date(2025, 1, 1), stance=Stance.UNSUPPORTED, margin_of_safety=None
    )
    prices = history(("2025-01-02", 100.0), ("2025-04-02", 120.0))

    outcome = resolve_outcome(decision, prices, 90)

    assert outcome.return_pct == 0.2
    assert outcome.stance_was_directionally_right is None


def test_resolve_outcome_returns_none_when_the_horizon_has_no_price_yet():
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(("2025-01-02", 100.0), ("2025-02-02", 110.0))

    assert resolve_outcome(decision, prices, 90) is None


def test_resolve_outcome_returns_none_when_history_starts_after_the_decision():
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(("2024-11-01", 90.0), ("2024-12-01", 95.0))

    assert resolve_outcome(decision, prices, 90) is None


def test_resolve_outcome_returns_none_for_empty_history():
    assert resolve_outcome(make_decision(), [], 90) is None


def test_resolve_outcome_uses_the_first_close_at_or_after_each_target_date():
    # Both ends land on non-trading days; each must snap forward to the
    # next real close rather than to whatever is merely nearest.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(
        ("2024-12-31", 50.0),
        ("2025-01-03", 100.0),
        ("2025-04-04", 110.0),
        ("2025-04-07", 200.0),
    )

    outcome = resolve_outcome(decision, prices, 90)

    assert outcome.price_at_decision == 100.0
    assert outcome.price_at_horizon == 110.0
    assert outcome.price_date_at_horizon == date(2025, 4, 4)


def test_resolve_outcome_refuses_to_divide_by_a_zero_starting_price():
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(("2025-01-02", 0.0), ("2025-04-02", 10.0))

    assert resolve_outcome(decision, prices, 90) is None


def test_horizon_days_is_recorded_so_outcomes_stay_comparable():
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(("2025-01-02", 100.0), ("2025-07-03", 150.0))

    outcome = resolve_outcome(decision, prices, horizon_days=180)

    assert outcome.horizon_days == 180


def test_write_reflection_records_the_lesson_model_and_token_cost():
    client = fake_llm("extreme negative margins track assumption error, not overvaluation")

    reflection = write_reflection(client, make_decision(), make_outcome(), model="test-model")

    assert reflection.lesson.startswith("extreme negative margins")
    assert reflection.model == "test-model"
    assert reflection.decision_id == make_decision().decision_id
    assert reflection.input_tokens == 300
    assert reflection.output_tokens == 40


def test_write_reflection_prompt_carries_the_decision_the_outcome_and_the_gap():
    client = fake_llm()
    decision = make_decision(ticker="NVO", as_of_date=date(2025, 1, 15))

    write_reflection(client, decision, make_outcome(return_pct=0.25))

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "NVO" in prompt
    assert "2025-01-15" in prompt
    assert "overvalued" in prompt
    assert "+25.0%" in prompt
    # The prompt must ask for a lesson that transfers to other companies,
    # not a verdict on this ticker.
    assert "FUTURE" in prompt
    assert "not just this one" in prompt


def test_write_reflection_prompt_never_leaks_risk_labels_or_source_text():
    # DecisionRecord stores counts by severity precisely so this can't
    # happen; this asserts the prompt builder doesn't reintroduce it.
    client = fake_llm()
    decision = make_decision()
    decision.qualitative_risk_counts = {"high": 2, "medium": 3}
    decision.qualitative_sources = ["10-K", "Q3 2025 earnings call"]

    write_reflection(client, decision, make_outcome())

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "'high': 2" in prompt
    assert "'medium': 3" in prompt
    # Counts reach the model; which documents produced them does not.
    assert "Q3 2025 earnings call" not in prompt
    assert "10-K" not in prompt


def test_write_reflection_renders_a_decision_with_no_computable_numbers():
    # UNSUPPORTED decisions have None intrinsic value and None MOS - the
    # prompt has to say so in words rather than crash on the format spec.
    client = fake_llm()
    decision = make_decision(stance=Stance.UNSUPPORTED, margin_of_safety=None)
    outcome = make_outcome()
    outcome.stance_was_directionally_right = None

    write_reflection(client, decision, outcome)

    prompt = client.messages.calls[0]["messages"][0]["content"]
    assert "not computable" in prompt
    assert "declined to value" in prompt


def test_drawdown_is_measured_against_the_running_high_not_the_entry_price():
    # Entry 100, rises to 150, falls to 90, ends at 120. Measured from
    # entry the worst is -10%; measured from the running high it is -40%,
    # which is what holding it actually felt like.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(
        ("2025-01-02", 100.0),
        ("2025-02-01", 150.0),
        ("2025-03-01", 90.0),
        ("2025-04-02", 120.0),
    )

    outcome = resolve_outcome(decision, prices, 90)

    assert outcome.return_pct == pytest.approx(0.2)
    assert outcome.max_drawdown_pct == pytest.approx(-0.4)
    assert outcome.trough_close == 90.0
    assert outcome.trough_date == date(2025, 3, 1)


def test_point_to_point_return_hides_a_drawdown_the_record_now_keeps():
    # The case the field exists for: identical return_pct, completely
    # different holding experience.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    calm = history(("2025-01-02", 100.0), ("2025-02-15", 101.0), ("2025-04-02", 100.0))
    violent = history(("2025-01-02", 100.0), ("2025-02-15", 60.0), ("2025-04-02", 100.0))

    calm_outcome = resolve_outcome(decision, calm, 90)
    violent_outcome = resolve_outcome(decision, violent, 90)

    assert calm_outcome.return_pct == violent_outcome.return_pct == pytest.approx(0.0)
    assert calm_outcome.max_drawdown_pct == pytest.approx(-0.0099, abs=1e-4)
    assert violent_outcome.max_drawdown_pct == pytest.approx(-0.4)


def test_a_window_that_only_rises_has_a_zero_drawdown_not_none():
    # 0.0 means "measured, nothing was lost"; None means "not
    # measurable". Collapsing them would make a clean run look like
    # missing data.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(("2025-01-02", 100.0), ("2025-02-01", 110.0), ("2025-04-02", 130.0))

    outcome = resolve_outcome(decision, prices, 90)

    assert outcome.max_drawdown_pct == 0.0
    assert outcome.trough_close == 100.0


def test_drawdown_only_considers_closes_inside_the_holding_window():
    # A crash before the decision, and another after the horizon, are
    # both somebody else's problem.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(
        ("2024-06-01", 10.0),
        ("2025-01-02", 100.0),
        ("2025-03-01", 95.0),
        ("2025-04-02", 120.0),
        ("2025-06-01", 5.0),
    )

    outcome = resolve_outcome(decision, prices, 90)

    assert outcome.max_drawdown_pct == pytest.approx(-0.05)
    assert outcome.closes_observed == 3


def test_closes_observed_exposes_a_window_too_sparse_to_trust():
    # Yahoo's range=max silently returns monthly closes; a drawdown
    # computed from two points understates the real one and nothing in
    # max_drawdown_pct would reveal that.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    prices = history(("2025-01-02", 100.0), ("2025-04-02", 120.0))

    outcome = resolve_outcome(decision, prices, 90)

    assert outcome.closes_observed == 2


def test_both_endpoints_snapping_to_one_close_refuses_instead_of_reporting_zero():
    # The real failure: the resolver fetched a 2-year history for
    # decisions up to 5 years old, so close_on_or_after() returned its
    # first point for both ends and 360 of 600 backtest cells resolved
    # to an exactly-0.00% return. Refusing leaves them resolvable later.
    decision = make_decision(as_of_date=date(2021, 1, 1))
    too_short = history(("2025-01-02", 100.0), ("2025-06-02", 150.0))

    assert resolve_outcome(decision, too_short, 90) is None


def test_a_window_with_two_distinct_closes_still_resolves():
    # The guard must reject only same-point windows, not thin ones.
    decision = make_decision(as_of_date=date(2025, 1, 1))
    thin = history(("2025-01-02", 100.0), ("2025-04-02", 110.0))

    outcome = resolve_outcome(decision, thin, 90)

    assert outcome is not None
    assert outcome.return_pct == pytest.approx(0.1)


def test_history_range_grows_with_the_oldest_pending_decision():
    today = date(2026, 11, 2)

    assert history_range_for(date(2026, 8, 1), today, 90) == "1y"
    assert history_range_for(date(2025, 6, 1), today, 90) == "2y"
    assert history_range_for(date(2023, 1, 1), today, 90) == "5y"
    assert history_range_for(date(2019, 1, 1), today, 90) == "10y"


def test_history_range_caps_at_ten_years_rather_than_asking_for_max():
    # range=max returns MONTHLY closes without saying so, which would
    # silently understate every drawdown measured from it.
    assert history_range_for(date(1995, 1, 1), date(2026, 11, 2), 90) == "10y"


def test_history_range_accounts_for_the_horizon_not_just_the_decision_date():
    # A decision just under a year old still needs closes past its
    # horizon, which can push it into the next range up.
    today = date(2026, 11, 2)
    almost_a_year = date(2025, 12, 1)

    assert history_range_for(almost_a_year, today, 0) == "1y"
    assert history_range_for(almost_a_year, today, 180) == "2y"
