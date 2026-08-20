from app.valuation.consensus import ValueRange
from scripts.analyze import format_range_chart


def test_format_range_chart_places_labels_at_bar_edges():
    ranges = [ValueRange(method="Comps", low=80, high=100)]

    chart = format_range_chart(ranges, width=50)

    assert "Comps" in chart
    assert "$80" in chart
    assert "$100" in chart
    # low label starts at the same column the bar's "├" is drawn on
    bar_line = next(line for line in chart.splitlines() if "├" in line)
    label_line = chart.splitlines()[chart.splitlines().index(bar_line) + 1]
    assert bar_line.index("├") == label_line.index("$80")


def test_format_range_chart_merges_labels_when_bar_too_narrow():
    # A bar this narrow relative to the overall span can't fit "$23" and
    # "$40" as two separate, non-overlapping labels.
    ranges = [
        ValueRange(method="DCF", low=23, high=40),
        ValueRange(method="Comps", low=139, high=511),
    ]

    chart = format_range_chart(ranges, width=50)

    assert "$23~$40" in chart


def test_format_range_chart_empty_ranges_returns_empty_string():
    assert format_range_chart([]) == ""


def test_format_range_chart_all_bars_stay_within_declared_width():
    ranges = [
        ValueRange(method="DCF (FCFF)", low=261, high=464),
        ValueRange(method="DCF (Owner Earnings)", low=232, high=414),
        ValueRange(method="Comps", low=236, high=264),
    ]

    chart = format_range_chart(ranges, width=50)

    name_width = max(len(r.method) for r in ranges)
    label_col = name_width + 2
    for line in chart.splitlines():
        if "├" in line:
            bar_part = line[label_col:]
            assert len(bar_part) <= 50
