from app.qualitative.sentiment import score_sentiment, split_sentences
from tests.factories import fake_sentiment_classifier


def test_split_sentences_handles_financial_abbreviations():
    text = (
        "The U.S. Dept. of Commerce opened an investigation. "
        "Mr. Cook said the impact was material. "
        "Net sales were $64.4 billion, down 4% year-over-year."
    )

    sentences = split_sentences(text)

    assert sentences == [
        "The U.S. Dept. of Commerce opened an investigation.",
        "Mr. Cook said the impact was material.",
        "Net sales were $64.4 billion, down 4% year-over-year.",
    ]


def test_split_sentences_filters_short_junk():
    text = "Page 6. Item 1A. The Company faces significant and ongoing competitive pressure."

    sentences = split_sentences(text)

    assert all(len(s.split()) >= 4 for s in sentences)
    assert "The Company faces significant and ongoing competitive pressure." in sentences


def test_score_sentiment_aggregates_counts_and_ratio():
    text = (
        "Competition has intensified across all our product categories this year. "
        "Services revenue grew strongly across every geographic segment. "
        "The Company faces material litigation risk in several jurisdictions."
    )
    classifier = fake_sentiment_classifier(
        [("negative", 0.9), ("positive", 0.95), ("negative", 0.8)]
    )

    result = score_sentiment(text, "10-K", classifier=classifier)

    assert result.sentence_count == 3
    assert result.negative_count == 2
    assert result.positive_count == 1
    assert result.neutral_count == 0
    assert result.negative_ratio == 2 / 3
    assert result.source_label == "10-K"


def test_score_sentiment_most_negative_sorted_by_score():
    text = (
        "Competition has intensified across all our product categories this year. "
        "The Company faces severe and escalating litigation risk in multiple regions. "
        "Regulatory scrutiny has increased modestly in some jurisdictions this year."
    )
    classifier = fake_sentiment_classifier(
        [("negative", 0.6), ("negative", 0.95), ("negative", 0.7)]
    )

    result = score_sentiment(text, "10-K", top_n_negative=2, classifier=classifier)

    assert len(result.most_negative) == 2
    assert result.most_negative[0].score == 0.95
    assert result.most_negative[1].score == 0.7


def test_score_sentiment_empty_text_returns_zeroed_summary():
    result = score_sentiment("", "10-K", classifier=fake_sentiment_classifier([]))

    assert result.sentence_count == 0
    assert result.negative_ratio == 0.0
    assert result.most_negative == []
