from app.valuation.consensus import ValueRange, compute_consensus


def test_consensus_overlap_across_three_overlapping_ranges():
    ranges = [
        ValueRange(method="DCF", low=90, high=115),
        ValueRange(method="Comps", low=80, high=100),
        ValueRange(method="Owner Earnings", low=95, high=120),
    ]

    result = compute_consensus(ranges)

    # overlap = [max(90,80,95), min(115,100,120)] = [95, 100]
    assert result.overlap_low == 95
    assert result.overlap_high == 100
    assert result.warnings == []


def test_consensus_no_overlap_reports_gap_explicitly():
    ranges = [
        ValueRange(method="DCF", low=90, high=100),
        ValueRange(method="Comps", low=150, high=200),
    ]

    result = compute_consensus(ranges)

    assert result.overlap_low is None
    assert result.overlap_high is None
    assert any("no overlap" in w for w in result.warnings)


def test_consensus_single_method_returns_its_own_range_with_warning():
    ranges = [ValueRange(method="DCF", low=90, high=115)]

    result = compute_consensus(ranges)

    assert result.overlap_low == 90
    assert result.overlap_high == 115
    assert any("only one valuation method" in w for w in result.warnings)


def test_consensus_no_ranges_returns_empty_with_warning():
    result = compute_consensus([])

    assert result.ranges == []
    assert result.overlap_low is None
    assert result.overlap_high is None
    assert any("no valuation method produced a range" in w for w in result.warnings)


def test_consensus_touching_ranges_produce_single_point_overlap():
    ranges = [
        ValueRange(method="DCF", low=90, high=100),
        ValueRange(method="Comps", low=100, high=120),
    ]

    result = compute_consensus(ranges)

    assert result.overlap_low == 100
    assert result.overlap_high == 100
