"""Read a resolved decision log and report whether margin of safety
predicted anything.

    python scripts/backtest_report.py --log data/backtest.jsonl
    python scripts/backtest_report.py --log data/backtest.jsonl --by-ticker

The headline number is EXCESS return, not raw return, and that choice is
the whole point of this script. Over 2021-2026 almost everything in a
large-cap US universe went up, so "decisions calling things overvalued
were wrong" is a statement about the market, not about this tool. The
only question a backtest can honestly answer is whether the names this
tool liked beat the names it disliked, over the same windows.

So each decision is measured against the equal-weighted mean of every
OTHER decision on the same as_of_date - leave-one-out, so a cell cannot
be part of its own benchmark. A tool with no information produces
excess returns centered on zero in every margin-of-safety bucket. One
with information produces a gradient across them.

What this script does NOT do is tell you a result is statistically
significant. It prints spreads and sample sizes. Cross-sectional stock
returns on the same date are strongly correlated (one market factor
moves everything), so the effective sample is nearer the number of
distinct dates than the number of cells, and an ordinary t-test over
cells would overstate confidence by a wide margin. Read the gradient
and the N; do not read a p-value into it.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from app.memory.decision_log import decisions, read_records
from app.memory.models import OutcomeRecord, Stance

# Open-ended at both ends because real output reaches past -100%: a DCF
# intrinsic value well below price makes (intrinsic - price) / intrinsic
# arbitrarily negative. Measured on AAPL at -131% and -141%.
BUCKETS = (
    ("MOS > 0 (undervalued)", 0.0, float("inf")),
    ("-25% .. 0%", -0.25, 0.0),
    ("-50% .. -25%", -0.50, -0.25),
    ("-100% .. -50%", -1.00, -0.50),
    ("below -100%", float("-inf"), -1.00),
)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def ranks(values: list[float]) -> list[float]:
    """Average ranks, so ties don't bias the correlation."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            result[order[k]] = shared
        i = j + 1
    return result


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation on ranks. Pure Python on purpose - this is the
    only statistic here and it isn't worth a numpy dependency.
    """
    if len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = mean(rx), mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy) ** 0.5


def pct(value: float | None, width: int = 7) -> str:
    return "     - " if value is None else f"{value:+{width}.2%}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="data/backtest.jsonl", help="decision log to read")
    parser.add_argument(
        "--by-ticker", action="store_true",
        help="also break results down per ticker (noisy; mostly useful for spotting "
        "one name dominating the sample)",
    )
    args = parser.parse_args()

    log_path = Path(args.log)
    records, warnings = read_records(log_path)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not records:
        print(f"nothing in {log_path} - run scripts/backtest.py first", file=sys.stderr)
        return 1

    outcome_by_id = {r.decision_id: r for r in records if isinstance(r, OutcomeRecord)}
    all_decisions = decisions(records)
    paired = [(d, outcome_by_id[d.decision_id]) for d in all_decisions if d.decision_id in outcome_by_id]

    print(f"log: {log_path}")
    print(f"{len(all_decisions)} decision(s), {len(paired)} resolved")
    if not paired:
        print(
            "\nnothing resolved yet - run:\n"
            f"  DECISION_LOG_PATH={log_path} python scripts/resolve_decisions.py --no-reflect",
            file=sys.stderr,
        )
        return 1

    scoreable = [(d, o) for d, o in paired if d.margin_of_safety is not None]
    unsupported = len(paired) - len(scoreable)
    dates = sorted({d.as_of_date for d, _ in scoreable})
    tickers = sorted({d.ticker for d, _ in scoreable})
    horizons = sorted({o.horizon_days for _, o in scoreable})
    print(f"{len(scoreable)} with a margin of safety, {unsupported} unsupported (no call made)")
    print(f"{len(tickers)} ticker(s), {len(dates)} date(s), horizon(s) {horizons}")
    if not scoreable:
        return 1

    # Leave-one-out same-date baseline: the market move common to every
    # name on that date, with the cell itself removed so it cannot
    # benchmark against its own return.
    by_date: dict[object, list[float]] = defaultdict(list)
    for d, o in scoreable:
        by_date[d.as_of_date].append(o.return_pct)

    rows = []
    for d, o in scoreable:
        same_date = by_date[d.as_of_date]
        if len(same_date) < 2:
            continue  # no peers that date, so no honest baseline
        baseline = (sum(same_date) - o.return_pct) / (len(same_date) - 1)
        rows.append(
            {
                "ticker": d.ticker,
                "as_of": d.as_of_date,
                "mos": d.margin_of_safety,
                "ret": o.return_pct,
                "excess": o.return_pct - baseline,
                "drawdown": o.max_drawdown_pct,
                "right": o.stance_was_directionally_right,
                "stance": d.stance,
            }
        )

    dropped = len(scoreable) - len(rows)
    if dropped:
        print(f"{dropped} cell(s) had no same-date peer to benchmark against - excluded")
    if not rows:
        print("\nno cell has a same-date peer; a one-ticker backtest cannot separate "
              "signal from market direction", file=sys.stderr)
        return 1

    print()
    print("Forward return by margin-of-safety bucket")
    print("(excess = this name minus the equal-weighted mean of other names, same date)")
    print()
    header = f"{'bucket':<24}{'N':>5}{'raw ret':>11}{'excess':>11}{'worst DD':>11}{'ret>0':>8}"
    print(header)
    print("-" * len(header))
    for label, low, high in BUCKETS:
        in_bucket = [r for r in rows if low <= r["mos"] < high]
        if not in_bucket:
            print(f"{label:<24}{0:>5}")
            continue
        dds = [r["drawdown"] for r in in_bucket if r["drawdown"] is not None]
        positive = sum(1 for r in in_bucket if r["ret"] > 0) / len(in_bucket)
        print(
            f"{label:<24}{len(in_bucket):>5}"
            f"{pct(median([r['ret'] for r in in_bucket])):>11}"
            f"{pct(median([r['excess'] for r in in_bucket])):>11}"
            f"{pct(median(dds)):>11}"
            f"{positive:>7.0%} "
        )
    print("-" * len(header))
    all_dds = [r["drawdown"] for r in rows if r["drawdown"] is not None]
    print(
        f"{'all':<24}{len(rows):>5}"
        f"{pct(median([r['ret'] for r in rows])):>11}"
        f"{pct(median([r['excess'] for r in rows])):>11}"
        f"{pct(median(all_dds)):>11}"
        f"{sum(1 for r in rows if r['ret'] > 0) / len(rows):>7.0%} "
    )

    rho = spearman([r["mos"] for r in rows], [r["excess"] for r in rows])
    print()
    print("Does a higher margin of safety go with a better excess return?")
    if rho is None:
        print("  not computable on this sample")
    else:
        direction = (
            "yes, in the direction the method predicts" if rho > 0.05
            else "no - the relationship runs backwards" if rho < -0.05
            else "no relationship worth reading"
        )
        print(f"  Spearman rank correlation: {rho:+.3f} -> {direction}")
    print("  (not a significance test - see this script's docstring)")

    stance_rows = [r for r in rows if r["right"] is not None]
    if stance_rows:
        hits = sum(1 for r in stance_rows if r["right"])
        print()
        print(
            f"Directional hit rate: {hits}/{len(stance_rows)} = "
            f"{hits / len(stance_rows):.0%} (coin flip is 50%)"
        )
        print(
            "  Split by stance below, but read it against the excess column, not alone: "
            "in a rising\n  market an 'undervalued' call is right for being long, "
            "which is beta, not skill."
        )
        for stance in (Stance.UNDERVALUED, Stance.OVERVALUED):
            subset = [r for r in stance_rows if r["stance"] == stance]
            if subset:
                sub_hits = sum(1 for r in subset if r["right"])
                print(
                    f"  called {stance.value:<12} {len(subset):>4} time(s), "
                    f"right {sub_hits / len(subset):.0%}"
                )

    if args.by_ticker:
        print()
        print(f"{'ticker':<8}{'N':>4}{'med MOS':>10}{'med excess':>12}{'med DD':>10}")
        for ticker in tickers:
            subset = [r for r in rows if r["ticker"] == ticker]
            if not subset:
                continue
            dds = [r["drawdown"] for r in subset if r["drawdown"] is not None]
            print(
                f"{ticker:<8}{len(subset):>4}"
                f"{pct(median([r['mos'] for r in subset]), 9):>10}"
                f"{pct(median([r['excess'] for r in subset]), 11):>12}"
                f"{pct(median(dds), 9):>10}"
            )

    print()
    print("Read this with the caveats, not around them:")
    print("  - The universe is today's index membership, picked partly for having done")
    print("    well. Excess return cancels most of that; raw return does not.")
    horizon_text = " / ".join(f"{h}d" for h in horizons)
    print(f"  - Horizon {horizon_text} only. A DCF is a claim about a decade of cash flows,")
    print("    and this measures a fraction of that.")
    print("  - Same-date returns are strongly correlated, so the effective sample is")
    print(f"    nearer {len(dates)} (dates) than {len(rows)} (cells).")
    print("  - compute_wacc/compute_comps were off during the backtest; they would have")
    print("    leaked today's market data into past decisions. See scripts/backtest.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
