# The decision log

An append-only record of what this system concluded, what the price
actually did afterwards, and what it should learn from the gap.

It exists because of a specific, reproducible problem. Run the default
assumptions against quality large caps and the model calls almost all of
them overvalued by wide margins — AAPL at −82% margin of safety, MSFT at
−209%. Those are not subtle errors that a better tag mapping would fix;
they are the model's own assumptions (a flat 9% discount rate, 5%
growth, 2.5% terminal growth) meeting companies those assumptions do not
describe. Nothing in the codebase could previously notice this, because
nothing kept score.

The log keeps score. It does not, and deliberately cannot, change any
number the valuation produces.

## The three record kinds

One JSONL stream, one record per line, discriminated by `kind` and
joined on `decision_id`:

| Kind | Written by | Holds |
|---|---|---|
| `decision` | `analysis_service.analyze(log_decision=True)` | Ticker, date, market price, stance, assumptions, intrinsic value, MOS, consensus methods, risk counts |
| `outcome` | `scripts/resolve_decisions.py` | Price at decision, price at the horizon, return, whether the stance was directionally right |
| `reflection` | `scripts/resolve_decisions.py` | One LLM-written lesson, plus the model and token cost that produced it |

Append-only in the event-sourced sense: resolving an outcome never
rewrites the decision line it refers to, it appends a new line keyed by
the same `decision_id`. Reading is replay-and-join. This is the same
property `restated_facts` gives financial data — a complete audit trail
of what was known when — applied to our own conclusions.

It also makes the file robust in the one way that matters for a process
that appends: a half-finished write can only ever damage the last line,
and `read_records()` skips an unparseable line with a warning rather
than failing the whole read.

## Running it

Log a decision (nothing is written without the flag):

```bash
python scripts/analyze.py AAPL 212 --log-decision
```

Later, score the decisions that are old enough and reflect on them:

```bash
python scripts/resolve_decisions.py
```

Resolution is free — it fetches price history from Yahoo and does
arithmetic. Reflection costs one Haiku call per decision, so it can be
skipped:

```bash
python scripts/resolve_decisions.py --no-reflect
python scripts/resolve_decisions.py --horizon 180
```

Both phases are idempotent. A decision that already has an outcome is
skipped, and so is one that already has a reflection, so re-running
after a partial failure (a rate limit, a dropped connection, an invalid
API key) only does the work still missing. Nothing is lost and nothing
is double-counted.

Feeding the lessons back in is a separate opt-in:

```bash
python scripts/analyze.py AAPL 212 --use-track-record
```

The same two flags exist on the API as `log_decision` and
`include_track_record`, both defaulting to `false`.

## What a lesson may and may not touch

This is the part worth being strict about.

The project's standing rule is that reference figures never silently
become inputs. Fundamental growth, WACC, and comps are all reported
*alongside* the valuation rather than merged into it, with
`use_wacc_as_discount_rate` as the single explicit exception. A track
record is the softest input yet — free text an LLM wrote about earlier
mistakes — so the rule binds hardest here.

A lesson reaches exactly one place: the qualitative extraction prompt,
where it may make the model more or less demanding about what counts as
a material risk. It never reaches `ValuationAssumptions`, never reaches
`margin_of_safety`, and never reaches the consensus. That is asserted
directly in `tests/unit/test_analysis_service_decision_log.py` —
`test_track_record_never_changes_the_assumptions_or_the_valuation` runs
the same analysis with and without a seeded lesson and compares the
numbers — because a future refactor could route it into assumptions
without any other test failing.

Two things keep it honest inside the prompt itself. The lessons block is
wrapped in framing that says these describe this analyst's historical
calibration, are not facts about the company, and are not evidence. And
every returned risk still needs a verbatim `supporting_quote` from the
filing, so a lesson alone can never manufacture one.

When a track record is used, the result says so in `warnings`. A caller
should be able to tell from the response alone that the model saw
something the filing did not contain.

## What is never stored

`app/api/analysis.py` promises that a request does not outlive the
request/response cycle. The decision log is the only thing in this app
that writes to disk, and it is built so that promise still holds:
qualitative risks are reduced to **counts by severity**. Never a risk
label, never a supporting quote, never any part of `earnings_call_text`
or the 10-K body, never an API key.

A decision record is a numbers-and-metadata artifact, not a copy of the
source material it was formed from. `test_logged_risks_are_counts_by_severity_with_no_labels_or_quotes`
checks this against the bytes on disk, not the parsed model.

## Scoring

`resolve_outcome()` is pure arithmetic over a price series — no network,
no LLM. It takes the first close on or after the decision date, the
first close on or after `as_of_date + horizon_days`, and computes the
return between them. Snapping *forward* to the next real close matters:
both endpoints routinely land on weekends and holidays, and picking
"whatever is nearest" would quietly let a decision be scored against a
price from before it was made.

If either endpoint is missing — the horizon has not arrived, or the
history does not reach back far enough — the decision stays unresolved
rather than being scored against a price that does not correspond to the
question asked.

`UNSUPPORTED` decisions (banks, missing FCFF inputs, no
`shares_outstanding`) get a return like any other but
`stance_was_directionally_right: null`. They made no call, so scoring
them either way would pollute the calibration the lessons are drawn
from. "We declined to value this" is still worth counting.

## Backtesting: the answer the live log can't give yet

The live log answers "is this system right" only after years of
waiting. A backtest answers it now, from history the SEC and Yahoo
already hold.

```bash
python scripts/backtest.py --years 5 --every 91
DECISION_LOG_PATH=data/backtest.jsonl python scripts/resolve_decisions.py --no-reflect
python scripts/backtest_report.py --log data/backtest.jsonl
```

No LLM is called, so the whole run is free. It writes to
`data/backtest.jsonl` rather than the live log, because a simulated
conclusion should not sit in the same file as a real one.

**`compute_wacc` and `compute_comps` are off during a backtest and must
stay off.** Neither reads financial statements, so the point-in-time
filter does not protect them: `compute_wacc` fetches today's 10-year
Treasury yield, and `compute_comps` applies peers' *current* prices as
multiples. Against a 2021 decision date both are tomorrow's information
informing yesterday's valuation. A backtest with them enabled would
look more sophisticated and measure nothing.

The report's headline is **excess** return — each decision against the
equal-weighted mean of every *other* decision on the same date,
leave-one-out so a cell is never part of its own benchmark. This is the
only honest framing: over 2021-2026 nearly everything in a large-cap US
universe rose, so "calls of overvalued were wrong" is a fact about the
market. The question a backtest can answer is whether the names this
tool liked beat the names it disliked over the same windows.

### What the first run found

DJIA 30, quarterly decision dates over 5 years, 600 decisions, 439 with
a margin of safety (161 unsupported — banks and missing FCFF inputs):

| Horizon | Spearman(MOS, excess return) | Directional hit rate |
|---|---|---|
| 90 days | +0.032 | 47% |
| 365 days | +0.042 | 43% |

No gradient across margin-of-safety buckets at either horizon, and the
bucket ordering is incoherent (at 365 days the best excess return came
from the −100%..−50% bucket, the worst from −50%..−25%), which is what
noise looks like. **With default assumptions, at these horizons, on
this universe, the margin of safety has no detectable predictive
power.**

Three things that result does *not* say:

- Not "DCF doesn't work." The run used one flat set of assumptions (9%
  discount, 5% growth) for all 30 companies, with the tool's own
  per-company reference figures deliberately disabled. It tests this
  configuration, not the method.
- Not "no edge exists anywhere." DJIA mega-caps are the most heavily
  covered, most efficiently priced segment of the market — the least
  likely place for a public-filings valuation to find mispricing.
- Not a significance test. Same-date stock returns are dominated by one
  market factor, so the effective sample is nearer the 20 distinct
  dates than the 439 cells.

A real finding worth separating out: median excess return is negative
(−1.06% at 90 days) while the mean is exactly zero by construction.
That is positive skew, not a defect — most names underperform the
average because a few large winners carry it.

## Where it lives

`data/decisions.jsonl` by default, overridable with `DECISION_LOG_PATH`.
Relative paths resolve against the project root, not the process's
working directory — the same anchoring `.env` uses, and for the same
reason: a relative default silently becomes a different file for every
directory the app is launched from, which would fragment a log instead
of accumulating one.

`data/` is gitignored. The log records what *this* instance concluded,
so committing it would mix one machine's history into everyone's
checkout.

It is a plain file rather than a database on purpose, and that choice
has a real cost on an ephemeral filesystem — see
[LIMITATIONS.md](LIMITATIONS.md).
