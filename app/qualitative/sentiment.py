"""FinBERT sentence-level sentiment - a fast, free, local first pass.

Self-hosted via `transformers` rather than a hosted inference API: no
extra API key/account beyond what this project already needs, no
per-call cost, and it fits FinBERT's original role in the V2 plan as a
cheap pre-check that can run before (or independently of) the paid,
slower LLM call - not a replacement for it.

FinBERT's practical input limit is ~512 tokens, far short of a full
10-K (~50k+ tokens), so documents are split into sentences and
classified one at a time. Spiked against a real AAPL 10-K: 925 usable
sentences (filtering out page-number/table-fragment junk under 4
words), split in ~12s and classified in ~11s on CPU once the model was
loaded - see docs/DATA_SPIKE_NOTES.md's V2 section. Sentence splitting
uses pysbd rather than a naive regex, since financial text is full of
abbreviations ("U.S.", "Mr.", "Dept.") that break naive ". "-splitting.

`transformers`/`torch` are an optional extra (`pip install -e
".[sentiment]"`), imported lazily inside _get_classifier() rather than
at module load time, so importing this module (e.g. just for the
pydantic result types, or calling split_sentences()) doesn't require
that ~1GB dependency install. pysbd is small enough to be a core
dependency.
"""

from enum import Enum
from functools import lru_cache

import pysbd
from pydantic import BaseModel

MODEL_NAME = "ProsusAI/finbert"
MIN_WORDS = 4  # filters page numbers/table fragments left over from HTML cleanup
DEFAULT_TOP_N_NEGATIVE = 5


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SentenceSentiment(BaseModel):
    text: str
    label: SentimentLabel
    score: float


class SentimentSummary(BaseModel):
    source_label: str
    sentence_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    negative_ratio: float
    most_negative: list[SentenceSentiment]


def split_sentences(text: str) -> list[str]:
    segmenter = pysbd.Segmenter(language="en", clean=True)
    return [s for s in segmenter.segment(text) if len(s.split()) >= MIN_WORDS]


def is_available() -> bool:
    """Whether the optional [sentiment] extra (transformers/torch) is installed."""
    try:
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache
def _get_classifier():
    from transformers import pipeline

    return pipeline("sentiment-analysis", model=MODEL_NAME)


def score_sentiment(
    text: str,
    source_label: str,
    top_n_negative: int = DEFAULT_TOP_N_NEGATIVE,
    classifier=None,
) -> SentimentSummary:
    sentences = split_sentences(text)
    if not sentences:
        return SentimentSummary(
            source_label=source_label,
            sentence_count=0,
            positive_count=0,
            negative_count=0,
            neutral_count=0,
            negative_ratio=0.0,
            most_negative=[],
        )

    classify = classifier or _get_classifier()
    raw_results = classify(sentences, truncation=True, batch_size=16)

    scored = [
        SentenceSentiment(text=s, label=r["label"], score=r["score"])
        for s, r in zip(sentences, raw_results, strict=True)
    ]

    positive_count = sum(1 for s in scored if s.label == SentimentLabel.POSITIVE)
    negative_count = sum(1 for s in scored if s.label == SentimentLabel.NEGATIVE)
    neutral_count = sum(1 for s in scored if s.label == SentimentLabel.NEUTRAL)

    most_negative = sorted(
        (s for s in scored if s.label == SentimentLabel.NEGATIVE),
        key=lambda s: -s.score,
    )[:top_n_negative]

    return SentimentSummary(
        source_label=source_label,
        sentence_count=len(scored),
        positive_count=positive_count,
        negative_count=negative_count,
        neutral_count=neutral_count,
        negative_ratio=negative_count / len(scored),
        most_negative=most_negative,
    )
