"""Ticker + market price in, a readable valuation summary out.

    python scripts/analyze.py AAPL 230

No server, no curl, no JSON - calls the same analyze() the HTTP API
uses, directly, with this project's own DEFAULT_ASSUMPTIONS (see
app/api/analysis.py) so results match what /api/v1/analyze would
return with no assumptions override. Not a full substitute for the API
(no sentiment/cross-validate options here) - just the fast path for
"one ticker, one price, what's the number," with optional qualitative
risk extraction bolted on:

    python scripts/analyze.py AAPL 230 --10k
    python scripts/analyze.py AAPL 230 --earnings-call transcript.txt

Both need ANTHROPIC_API_KEY set in .env (same key the API's
analyze_10k/earnings_call_text options use) - this script reads it via
the same app.config.get_settings() the API does, not a separate path.
"""

import argparse
import sys
from datetime import date

import anthropic

from app.api.analysis import DEFAULT_ASSUMPTIONS
from app.config import get_settings
from app.data.exceptions import SECClientError, UnknownTickerError
from app.data.market_data import build_default_market_data_client
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map
from app.qualitative.risk_extraction import QualitativeAnalysisError, QualitativeRiskAnalysis
from app.services.analysis_service import analyze
from app.valuation.consensus import ValueRange

CHART_WIDTH = 50


def _pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "n/a"


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def format_range_chart(ranges: list[ValueRange], width: int = CHART_WIDTH) -> str:
    """Renders each method's [low, high] as a horizontal bar, scaled to a
    shared axis across all of them - the "football field" chart real
    valuation teams use to compare methods at a glance.

        Comps        ├──────────┤
                     $80       $100

    Bar position is proportional to where [low, high] falls within the
    overall min-max span across all ranges passed in, so bars are
    visually comparable to each other, not just internally consistent.
    """
    if not ranges:
        return ""

    overall_low = min(r.low for r in ranges)
    overall_high = max(r.high for r in ranges)
    span = overall_high - overall_low or 1.0
    name_width = max(len(r.method) for r in ranges)
    label_col = name_width + 2

    def _place(chars: str, start: int, into: list[str]) -> None:
        # Grows the line rather than silently truncating - a label
        # ending near/at the bar's right edge must not lose characters.
        end = start + len(chars)
        if end > len(into):
            into.extend([" "] * (end - len(into)))
        for i, ch in enumerate(chars):
            into[start + i] = ch

    lines = [" " * label_col + "Valuation Range", ""]
    for r in ranges:
        start_col = round((r.low - overall_low) / span * (width - 1))
        end_col = round((r.high - overall_low) / span * (width - 1))
        end_col = max(end_col, start_col + 2)
        end_col = min(end_col, width - 1)

        bar = [" "] * width
        bar[start_col] = "├"
        bar[end_col] = "┤"
        for i in range(start_col + 1, end_col):
            bar[i] = "─"
        lines.append(r.method.ljust(name_width) + "  " + "".join(bar))

        low_label = f"${r.low:,.0f}"
        high_label = f"${r.high:,.0f}"
        num_line: list[str] = [" "] * label_col
        if end_col - start_col < len(low_label) + 1:
            # too narrow to place both labels separately without
            # overlapping - combine into one "$low~$high" label instead
            _place(f"{low_label}~{high_label}", label_col + start_col, num_line)
        else:
            _place(low_label, label_col + start_col, num_line)
            _place(high_label, label_col + end_col, num_line)
        lines.append("".join(num_line).rstrip())
        lines.append("")

    return "\n".join(lines).rstrip()


def _print_qualitative_analysis(analysis: QualitativeRiskAnalysis) -> None:
    print()
    print(f"--- qualitative risks: {analysis.source_label} (model: {analysis.model}) ---")
    print(analysis.summary)
    print()
    for risk in analysis.risks:
        print(f"  [{risk.severity.value.upper()}/{risk.status.value}] {risk.label}")
        print(f"    {risk.description}")
        print(f'    quote ({risk.grounding.value}): "{risk.supporting_quote}"')
    print(
        f"  ({analysis.input_tokens} input / {analysis.output_tokens} output tokens - "
        "real Anthropic API cost)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("market_price", type=float)
    parser.add_argument(
        "--10k", dest="analyze_10k", action="store_true",
        help="extract qualitative risks from the most recent 10-K (needs ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--earnings-call", dest="earnings_call_file", metavar="FILE",
        help="path to a text file with an earnings call transcript to analyze for risks "
        "(needs ANTHROPIC_API_KEY)",
    )
    args = parser.parse_args()

    earnings_call_text = None
    if args.earnings_call_file:
        try:
            with open(args.earnings_call_file, encoding="utf-8") as f:
                earnings_call_text = f.read()
        except OSError as exc:
            print(f"error: couldn't read --earnings-call file: {exc}", file=sys.stderr)
            return 1

    anthropic_client = None
    if args.analyze_10k or earnings_call_text:
        api_key = get_settings().anthropic_api_key
        if not api_key:
            print(
                "error: --10k/--earnings-call need ANTHROPIC_API_KEY set in .env "
                "(get one at console.anthropic.com)",
                file=sys.stderr,
            )
            return 1
        anthropic_client = anthropic.Anthropic(api_key=api_key)

    client = build_default_client()
    market_data_client = build_default_market_data_client()
    try:
        result = analyze(
            ticker=args.ticker.upper(),
            market_price=args.market_price,
            as_of_date=date.today(),  # noqa: DTZ011 - calendar date default is fine
            assumptions=DEFAULT_ASSUMPTIONS,
            client=client,
            ticker_map=build_default_ticker_map(),
            compute_comps=True,
            market_data_client=market_data_client,
            analyze_10k=args.analyze_10k,
            earnings_call_text=earnings_call_text,
            anthropic_client=anthropic_client,
        )
    except UnknownTickerError as exc:
        print(f"error: unknown ticker '{exc}' (not in the local ticker cache)", file=sys.stderr)
        return 1
    except SECClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except QualitativeAnalysisError as exc:
        print(f"error: qualitative analysis failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()
        market_data_client.close()

    print(f"=== {result.company.ticker} - {result.company.name} ===")
    print(f"valuation category: {result.company.valuation_category.value}")
    print()

    mos = result.margin_of_safety
    if mos is None:
        print(f"no valuation: {result.unsupported_reason}")
    else:
        print(f"market price:      {_money(args.market_price)}")
        print(
            f"intrinsic value:   {_money(mos.intrinsic_value_per_share)}  "
            f"(range: {_money(mos.intrinsic_value_low)} ~ {_money(mos.intrinsic_value_high)})"
        )
        print(
            f"margin of safety:  {_pct(mos.margin_of_safety)}  "
            f"(range: {_pct(mos.margin_of_safety_low)} ~ {_pct(mos.margin_of_safety_high)})"
        )
        print()
        print(
            "assumptions used:  "
            f"growth={_pct(DEFAULT_ASSUMPTIONS.fcff_growth_rate)}  "
            f"discount={_pct(DEFAULT_ASSUMPTIONS.discount_rate)}  "
            f"terminal={_pct(DEFAULT_ASSUMPTIONS.terminal_growth_rate)}  "
            f"tax={_pct(DEFAULT_ASSUMPTIONS.tax_rate)}"
        )

    growth = result.fundamental_growth_estimate
    print()
    print(f"fundamental growth estimate (reference only): {_pct(growth.suggested_growth_rate)}")
    # Warnings only for the years that actually fed suggested_growth_rate
    # (the most recent years_averaged computable years) - e.g. the "both
    # reinvestment_rate and ROIC negative" sign trap, where two negatives
    # multiply to a positive growth_rate that looks like healthy growth but
    # isn't. Not the full by_year list, which also carries an older,
    # unused year for every fiscal year with nothing computable.
    computable_years = [y for y in growth.by_year if y.growth_rate is not None]
    for year in computable_years[-growth.years_averaged :]:
        for warning in year.warnings:
            print(f"  ! FY{year.fiscal_year}: {warning}")

    # Only the warnings that bear on the number just printed (DCF/MOS-level -
    # terminal value dominance, look-ahead exclusions). The full response also
    # carries a warning per historical fiscal year with a missing XBRL tag
    # (visible via the HTTP API's `warnings` field) - too much noise for a
    # "just give me the number" CLI, so it's summarized as a count instead.
    if mos is not None and mos.warnings:
        print()
        print("warnings:")
        for warning in mos.warnings:
            print(f"  - {warning}")

    if result.warnings:
        print()
        print(
            f"({len(result.warnings)} additional per-fiscal-year data warning(s) omitted - "
            "use the HTTP API's `warnings` field for the full list)"
        )

    consensus = result.valuation_consensus
    if consensus.ranges:
        chart_ranges = list(consensus.ranges)
        if consensus.overlap_low is not None:
            chart_ranges.append(
                ValueRange(
                    method="Overlap", low=consensus.overlap_low, high=consensus.overlap_high
                )
            )
        print()
        print(format_range_chart(chart_ranges))
        if consensus.overlap_low is None:
            print()
            for warning in consensus.warnings:
                print(f"  ! {warning}")

    for analysis in result.qualitative_analyses:
        _print_qualitative_analysis(analysis)

    return 0


if __name__ == "__main__":
    sys.exit(main())
