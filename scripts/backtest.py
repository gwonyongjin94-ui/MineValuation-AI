"""Run this project's valuation across a universe of tickers at many
past dates, logging each conclusion as a decision.

    python scripts/backtest.py                       # DJIA 30, quarterly, 5 years
    python scripts/backtest.py --years 10 --every 180
    python scripts/backtest.py --tickers-file my_universe.txt
    python scripts/backtest.py --log data/backtest.jsonl

Then score what it produced, and read the result:

    DECISION_LOG_PATH=data/backtest.jsonl python scripts/resolve_decisions.py --no-reflect
    python scripts/backtest_report.py --log data/backtest.jsonl

Why this exists: the live decision log answers "was this system right"
only after years of waiting. A backtest answers it now, from history
the SEC and Yahoo already hold. Until it runs, every claim about
whether this tool's margin of safety predicts anything is unsupported -
with two live decisions on record, both wrong, the honest answer is
that nobody knows.

Free. No LLM is called anywhere in this script, by construction: it
passes analyze_10k=False and no earnings_call_text, so the only costs
are SEC and Yahoo requests.

WHAT IS DELIBERATELY OFF, and why it has to be:

- compute_wacc fetches today's 10-year Treasury yield from FRED. Used
  against a 2019 decision date, that is tomorrow's interest rate
  informing yesterday's valuation.
- compute_comps fetches peers' CURRENT prices from Yahoo and applies
  those multiples to a past date - the same leak, and it also multiplies
  the request count by the peer-list length.

Neither is fixed by the statement-level look-ahead filter in
app/financials/point_in_time.py, because neither reads statements: they
pull live market data. A backtest that enabled them would look more
sophisticated and measure nothing. So this script runs the paths that
can be made point-in-time honest - DCF(FCFF), Owner Earnings, H-Model,
Residual Income - and leaves the rest out.

Known bias that is NOT fixed here: the default universe is today's
DJIA, which was selected partly for having done well. Survivorship and
selection bias inflate every return in the sample, including the
baseline the report compares against - which is why the report's
headline number is excess return over the same universe on the same
dates, not raw return. See docs/LIMITATIONS.md.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from app.api.analysis import DEFAULT_ASSUMPTIONS
from app.data.exceptions import SECClientError, UnknownTickerError
from app.data.market_data import (
    MarketDataError,
    build_default_market_data_client,
    close_on_or_after,
    fetch_price_history,
)
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map
from app.memory.decision_log import append_record, read_records
from app.memory.models import DecisionRecord
from app.services.analysis_service import analyze as run_analysis
from app.services.analysis_service import build_decision_record
from app.valuation.dcf import UnsupportedValuationError

# Today's Dow Jones Industrial Average. A liquid, well-known, 30-name
# universe that keeps a first run small enough to finish in minutes -
# not a claim that it is an unbiased sample. Override with
# --tickers-file for anything you intend to draw conclusions from.
DJIA = (
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
    "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
)

DEFAULT_YEARS = 5
DEFAULT_EVERY_DAYS = 91  # ~quarterly: one decision per reporting cycle
DEFAULT_HORIZON_DAYS = 90
# Yahoo's chart endpoint returns daily closes up to 10y; range=max
# silently switches to monthly, which would quietly wreck the drawdown
# measurement (see app/memory/models.py on closes_observed).
MAX_HISTORY_YEARS = 10


class CachingSECClient:
    """Memoizes submissions/companyfacts per CIK for this process.

    One ticker gets valued at every date in the grid, and those two
    documents are identical across all of them - the as-of-date
    filtering happens downstream on the parsed statements, not at the
    fetch. Without this a 30x20 grid would be 1,200 SEC round trips
    instead of 60, for byte-identical responses.
    """

    def __init__(self, inner):
        self._inner = inner
        self._submissions: dict[str, dict] = {}
        self._facts: dict[str, dict] = {}

    def get_submissions(self, cik: str) -> dict:
        if cik not in self._submissions:
            self._submissions[cik] = self._inner.get_submissions(cik)
        return self._submissions[cik]

    def get_company_facts(self, cik: str) -> dict:
        if cik not in self._facts:
            self._facts[cik] = self._inner.get_company_facts(cik)
        return self._facts[cik]

    def __getattr__(self, name):
        return getattr(self._inner, name)


def decision_dates(years: int, every_days: int, horizon_days: int) -> list[date]:
    """Oldest first, stopping early enough that every decision can
    actually be scored.

    A decision needs `horizon_days` of price history after it to
    resolve; generating ones that cannot be scored yet would just leave
    permanent noise in the log.
    """
    today = date.today()  # noqa: DTZ011 - calendar date default is fine
    newest = today - timedelta(days=horizon_days + 5)
    oldest = today - timedelta(days=365 * years)
    dates = []
    current = oldest
    while current <= newest:
        dates.append(current)
        current += timedelta(days=every_days)
    return dates


def existing_keys(log_path) -> set[tuple[str, date]]:
    """(ticker, as_of_date) already in the log, so a re-run resumes
    instead of duplicating - a 30x20 grid takes a while and gets
    interrupted.
    """
    records, _ = read_records(log_path)
    return {
        (r.ticker, r.as_of_date) for r in records if isinstance(r, DecisionRecord)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years", type=int, default=DEFAULT_YEARS,
        help=f"how far back to start (default: {DEFAULT_YEARS}, max {MAX_HISTORY_YEARS})",
    )
    parser.add_argument(
        "--every", type=int, default=DEFAULT_EVERY_DAYS, dest="every_days",
        help=f"days between decision dates (default: {DEFAULT_EVERY_DAYS}, ~quarterly)",
    )
    parser.add_argument(
        "--horizon", type=int, default=DEFAULT_HORIZON_DAYS,
        help="the horizon these decisions will be scored at; decision dates stop this "
        f"far before today (default: {DEFAULT_HORIZON_DAYS})",
    )
    parser.add_argument(
        "--tickers-file", metavar="FILE",
        help="newline-separated tickers to use instead of the default DJIA 30",
    )
    parser.add_argument(
        "--log", metavar="PATH", default="data/backtest.jsonl",
        help="where to append decisions (default: data/backtest.jsonl - deliberately "
        "not the live decision log, so a backtest never pollutes real conclusions)",
    )
    parser.add_argument(
        "--limit-tickers", type=int, default=None,
        help="only the first N tickers of the universe (for a quick smoke run)",
    )
    args = parser.parse_args()

    if args.years > MAX_HISTORY_YEARS:
        print(
            f"error: --years above {MAX_HISTORY_YEARS} would need a price history range "
            "Yahoo only serves at monthly granularity, which silently breaks the "
            "drawdown measurement",
            file=sys.stderr,
        )
        return 1

    if args.tickers_file:
        try:
            with open(args.tickers_file, encoding="utf-8") as f:
                universe = [line.strip().upper() for line in f if line.strip()]
        except OSError as exc:
            print(f"error: couldn't read --tickers-file: {exc}", file=sys.stderr)
            return 1
    else:
        universe = list(DJIA)
    if args.limit_tickers:
        universe = universe[: args.limit_tickers]

    dates = decision_dates(args.years, args.every_days, args.horizon)
    if not dates:
        print("error: no decision dates in that range", file=sys.stderr)
        return 1

    log_path = Path(args.log)
    already = existing_keys(log_path)

    print(f"{len(universe)} ticker(s) x {len(dates)} date(s) = {len(universe) * len(dates)} cells")
    print(f"dates: {dates[0]} .. {dates[-1]} every {args.every_days}d, horizon {args.horizon}d")
    print(f"log:   {log_path}")
    if already:
        print(f"resuming: {len(already)} cell(s) already logged, will be skipped")
    print()

    ticker_map = build_default_ticker_map()
    sec_client = CachingSECClient(build_default_client())
    market_data_client = build_default_market_data_client()

    history_range = f"{max(args.years + 1, 2)}y"
    logged = unsupported = skipped = no_price = failed = 0

    try:
        for ticker in universe:
            try:
                history = fetch_price_history(ticker, market_data_client, range_=history_range)
            except MarketDataError as exc:
                print(f"{ticker}: no price history ({exc}) - skipping every date")
                failed += len(dates)
                continue

            per_ticker = {"logged": 0, "unsupported": 0, "no_price": 0, "failed": 0, "skipped": 0}
            for as_of in dates:
                if (ticker, as_of) in already:
                    per_ticker["skipped"] += 1
                    skipped += 1
                    continue

                point = close_on_or_after(history, as_of)
                if point is None:
                    per_ticker["no_price"] += 1
                    no_price += 1
                    continue

                try:
                    result = run_analysis(
                        ticker=ticker,
                        market_price=point.close,
                        as_of_date=as_of,
                        assumptions=DEFAULT_ASSUMPTIONS,
                        client=sec_client,
                        ticker_map=ticker_map,
                        # Everything below is off on purpose - see the
                        # module docstring. compute_wacc/compute_comps
                        # would leak today's market data into a past
                        # decision; the LLM paths would cost money.
                        compute_wacc=False,
                        compute_comps=False,
                        analyze_10k=False,
                        market_data_client=market_data_client,
                    )
                except (UnsupportedValuationError, UnknownTickerError) as exc:
                    per_ticker["unsupported"] += 1
                    unsupported += 1
                    if per_ticker["unsupported"] == 1:
                        print(f"{ticker}: unsupported ({exc})")
                    continue
                except SECClientError as exc:
                    per_ticker["failed"] += 1
                    failed += 1
                    print(f"{ticker} {as_of}: SEC error ({exc})")
                    continue

                append_record(
                    build_decision_record(
                        result,
                        ticker=ticker,
                        as_of_date=as_of,
                        market_price=point.close,
                        assumptions=DEFAULT_ASSUMPTIONS,
                        used_wacc_as_discount_rate=False,
                    ),
                    log_path,
                )
                per_ticker["logged"] += 1
                logged += 1

            summary = ", ".join(f"{k}={v}" for k, v in per_ticker.items() if v)
            print(f"{ticker:>5}: {summary or 'nothing to do'}")
    except KeyboardInterrupt:
        print("\ninterrupted - progress is on disk, re-run to resume", file=sys.stderr)
    finally:
        sec_client.close()
        market_data_client.close()

    print()
    print(f"logged {logged} decision(s)")
    if skipped:
        print(f"  {skipped} already present")
    if unsupported:
        print(f"  {unsupported} unsupported (banks, missing FCFF inputs, no filings yet)")
    if no_price:
        print(f"  {no_price} had no price at that date")
    if failed:
        print(f"  {failed} failed on a data error")
    print()
    print("next:")
    print(f"  DECISION_LOG_PATH={log_path} python scripts/resolve_decisions.py --no-reflect")
    print(f"  python scripts/backtest_report.py --log {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
