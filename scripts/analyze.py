"""Ticker + market price in, a readable valuation summary out.

    python scripts/analyze.py AAPL 230

No server, no curl, no JSON - calls the same analyze() the HTTP API
uses, directly, with this project's own DEFAULT_ASSUMPTIONS (see
app/api/analysis.py) so results match what /api/v1/analyze would
return with no assumptions override. Not a substitute for the API
(no qualitative/sentiment/cross-validate options here) - just the
fast path for "one ticker, one price, what's the number."
"""

import argparse
import sys
from datetime import date

from app.api.analysis import DEFAULT_ASSUMPTIONS
from app.data.exceptions import SECClientError, UnknownTickerError
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map
from app.services.analysis_service import analyze


def _pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "n/a"


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("market_price", type=float)
    args = parser.parse_args()

    client = build_default_client()
    try:
        result = analyze(
            ticker=args.ticker.upper(),
            market_price=args.market_price,
            as_of_date=date.today(),  # noqa: DTZ011 - calendar date default is fine
            assumptions=DEFAULT_ASSUMPTIONS,
            client=client,
            ticker_map=build_default_ticker_map(),
        )
    except UnknownTickerError as exc:
        print(f"error: unknown ticker '{exc}' (not in the local ticker cache)", file=sys.stderr)
        return 1
    except SECClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
