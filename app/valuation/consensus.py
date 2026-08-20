"""Cross-method valuation consensus: given each method's own
conservative-to-optimistic value-per-share range (DCF, Owner Earnings
DCF, Comps - see their own modules), computes the overlap across all of
them - the sub-range every available method's range agrees on, the
same "football field" idea real institutions use when they run DCF,
comps, and precedent transactions side by side (see the JPM/BlackRock/
Buffett discussion this whole ranges-and-intersection feature came out
of).

Intersection of N ranges [low_i, high_i] is [max(low_i), min(high_i)] -
empty whenever max(low_i) > min(high_i), which is reported explicitly
as "no overlap", not silently dropped. Methods disagreeing entirely is
itself a real, useful signal, not a computation failure.
"""

from pydantic import BaseModel


class ValueRange(BaseModel):
    method: str
    low: float
    high: float


class ValuationConsensus(BaseModel):
    ranges: list[ValueRange]
    overlap_low: float | None = None
    overlap_high: float | None = None
    warnings: list[str] = []


def compute_consensus(ranges: list[ValueRange]) -> ValuationConsensus:
    if not ranges:
        return ValuationConsensus(ranges=[], warnings=["no valuation method produced a range"])

    overlap_low = max(r.low for r in ranges)
    overlap_high = min(r.high for r in ranges)

    if len(ranges) < 2:
        return ValuationConsensus(
            ranges=ranges,
            overlap_low=overlap_low,
            overlap_high=overlap_high,
            warnings=[
                "only one valuation method available - no cross-method agreement to check"
            ],
        )

    if overlap_low > overlap_high:
        return ValuationConsensus(
            ranges=ranges,
            overlap_low=None,
            overlap_high=None,
            warnings=[
                (
                    f"no overlap across methods - ranges do not agree (highest low bound "
                    f"{overlap_low:.2f} exceeds lowest high bound {overlap_high:.2f})"
                )
            ],
        )

    return ValuationConsensus(ranges=ranges, overlap_low=overlap_low, overlap_high=overlap_high)
