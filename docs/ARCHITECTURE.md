# Architecture

## Layers

```
app/api/            HTTP boundary only - request/response models, status codes
app/services/        orchestration - wires the layers below into one call
app/data/            SEC access + raw-fact schema (FinancialFact, FinancialStatement)
app/financials/      raw facts -> normalized statement -> pure analysis metrics
app/valuation/        FCFF -> DCF -> margin of safety
app/qualitative/      V2: LLM risk extraction + FinBERT sentiment, from raw text
```

Each layer only knows about the layer directly below it, and none of
`app/data`, `app/financials`, or `app/valuation` import anything from
`app/api`. That means the valuation engine can be exercised from a
script or a test without FastAPI in the loop at all - every integration
test in `tests/integration/` except the API ones does exactly that.

The layering exists to keep three kinds of error separable, per the
project's original design goal:

```
SEC data error       (bad ticker, SEC down, malformed JSON)
Normalization error   (wrong XBRL tag picked, missing concept)
Valuation error        (bad assumption, financial company, no data)
```

A bug in one layer should never require reading the others to
diagnose - the exception types and warning messages are layer-specific
(see `app/data/exceptions.py` vs `UnsupportedValuationError` in
`app/valuation/dcf.py`).

## Data flow

```
ticker
  │
  ▼
TickerMap.resolve()              app/data/ticker_map.py
  │  (local cache of SEC's company_tickers.json; raises
  │   UnknownTickerError if the ticker isn't in it)
  ▼
CIK
  │
  ▼
SECClient.get_submissions()      app/data/sec_client.py
SECClient.get_company_facts()     (throttled, explicit exceptions per
  │                                 failure mode - see DATA_MODEL.md)
  ▼
raw submissions + companyfacts JSON
  │
  ▼
normalize()                      app/financials/normalizer.py
  │  raw XBRL facts -> list[FinancialStatement], one per fiscal year,
  │  with full provenance and an explicit valuation_category
  ▼
list[FinancialStatement]
  │
  ├──────────────────────────────┐
  ▼                                ▼
compute_metrics()                 compute_margin_of_safety()
app/financials/metrics.py          app/valuation/margin_of_safety.py
(pure ratios, no assumptions)      │  filters look-ahead statements,
  │                                 │  then:
  ▼                                 ▼
list[YearMetrics]                  compute_fcff_series() -> select_base_fcff()
                                     -> run_dcf() -> run_sensitivity()
                                     app/valuation/{fcff,dcf}.py
                                     │
                                     ▼
                                    MarginOfSafetyResult
  │                                 │
  └──────────────┬──────────────────┘
                 ▼
        AnalysisResult                app/services/analysis_service.py
                 │
                 ▼
        AnalyzeResponse (HTTP JSON)   app/api/analysis.py
```

## Module responsibilities

| Module | Responsibility | Does NOT do |
|---|---|---|
| `data/sec_client.py` | HTTP to SEC EDGAR, throttling, error mapping | Know what a "revenue" tag is |
| `data/ticker_map.py` | ticker -> CIK, cached locally | Know anything about financials |
| `data/models.py` | Schema for a normalized fact/statement | Select which tag to use |
| `financials/normalizer.py` | Pick the right XBRL fact per metric/period | Compute ratios or value anything |
| `financials/metrics.py` | Pure ratios (margins, growth, simple FCF) | Use a discount rate or forecast |
| `valuation/assumptions.py` | Typed, validated assumption inputs | Contain a growth/discount/tax number itself |
| `valuation/fcff.py` | FCFF per statement, base-FCFF selection | Discount anything |
| `valuation/dcf.py` | Project, discount, terminal value, sensitivity | Know about market price |
| `valuation/margin_of_safety.py` | market price + as_of_date -> MOS range | Fetch market data |
| `qualitative/risk_extraction.py` | Text -> structured qualitative risks (forced tool-use) | Fetch text, know its source |
| `qualitative/sentiment.py` | Text -> FinBERT sentence-level sentiment | Fetch text, know its source |
| `services/analysis_service.py` | Call the above in order, assemble one result | Know about HTTP status codes |
| `api/analysis.py` | Request/response models, error -> HTTP status | Compute anything |

## Why FCFF/DCF is not always run

`FinancialStatement.company.valuation_category` (`STANDARD` / `FINANCIAL`
/ `UNSUPPORTED`) is decided from the SEC-reported SIC code before any
metric extraction is attempted, not after a failed DCF run. This was
an empirical decision, not a theoretical one - see
[DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) finding #6: a real bank's
`companyfacts` has no `OperatingIncomeLoss`, no `AssetsCurrent` /
`LiabilitiesCurrent`, and no capex tag at all, so there is nothing to
force a standard-company DCF onto in the first place.
`run_dcf_valuation()` raises `UnsupportedValuationError` for anything
that isn't `STANDARD`, and the API layer turns that into a normal
`200` response with `margin_of_safety: null` and an explanatory
`unsupported_reason` - not an error, since "this is a bank" is a valid
and useful answer.

## V2: qualitative layer

```
POST /api/v1/analyze { analyze_10k: true, earnings_call_text?: "...", include_sentiment?: true }
  │
  ├─ analyze_10k=true
  │    │
  │    ▼
  │  list_recent_filings() + fetch_filing_document()   app/data/filing_documents.py
  │    │  (raw 10-K/10-Q HTML -> cleaned plain text; no section-level
  │    │   extraction - see DATA_MODEL.md for why)
  │    ▼
  │  extract_risks(text, "10-K")                       app/qualitative/risk_extraction.py
  │  score_sentiment(text, "10-K")  [if include_sentiment] app/qualitative/sentiment.py
  │
  └─ earnings_call_text="..." (user-pasted, not fetched - no free SEC
       source for earnings calls)
       │
       ▼
     extract_risks(text, "Earnings call (user-provided)")
     score_sentiment(...)  [if include_sentiment]
```

Both qualitative paths run independently of the quantitative DCF/MOS
path and are never merged into it numerically - `analysis_service.analyze()`
returns `qualitative_analyses` and `sentiment_analyses` as separate lists
alongside `margin_of_safety`, and only adds a `warnings` entry when 2+
high-severity risks are found. See VALUATION_METHOD.md for why there is
no "Adjusted Margin of Safety" formula.

`extract_risks()`/`score_sentiment()` take a client/classifier as an
explicit parameter (`anthropic_client`, `sentiment_classifier`) rather
than constructing one internally - the same dependency-injection pattern
`SECClient`/`TickerMap` already use, and for the same reason: tests
inject a fake instead of hitting a real (paid, or ~1GB-model-loading)
backend. `app/api/analysis.py` wires the real ones via FastAPI
`Depends()` (`get_anthropic_client`, `get_sentiment_classifier`).

Both qualitative features are opt-in per request and gated on server
configuration: `analyze_10k`/`earnings_call_text` need `ANTHROPIC_API_KEY`
set, `include_sentiment` needs the optional `[sentiment]` extra
(`transformers`/`torch`) installed. Requesting either without the
prerequisite returns `503`, not a crash or silent no-op.

## Configuration

`app/config.py`'s `Settings` reads `.env` from a path anchored to the
project root (`Path(__file__).resolve().parent.parent`), not a bare
relative `".env"` resolved against the process's current working
directory. That distinction is not theoretical: launching uvicorn with
`--app-dir` from outside the project directory (as this project's own
`.claude/launch.json` does, to work with the run/preview tooling)
reproduced a `500` on every request until this was fixed - pydantic-settings
resolves a relative `env_file` against cwd, and cwd was never guaranteed
to be the project root.
