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
