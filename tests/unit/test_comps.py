import httpx
import pytest

from app.data.sec_client import SECClient
from app.financials.normalizer import normalize
from app.valuation.comps import estimate_comps, peers_for_sic
from tests.factories import build_ticker_map_with_cache, sec_entry, sec_facts

TARGET_SIC = "3674"  # Semiconductor - matches wacc.py's real INDUSTRY_PEERS entry


def _submissions(ticker: str, sic: str) -> dict:
    return {"tickers": [ticker], "name": f"{ticker} Co", "sic": sic, "sicDescription": "Test"}


def _facts(revenue, net_income, operating_income, d_and_a, shares, *, cash=0, debt=0) -> dict:
    tags = {
        "Revenues": [sec_entry(revenue, "2025-12-31", 2025, start="2025-01-01")],
        "NetIncomeLoss": [sec_entry(net_income, "2025-12-31", 2025, start="2025-01-01")],
        "OperatingIncomeLoss": [
            sec_entry(operating_income, "2025-12-31", 2025, start="2025-01-01")
        ],
        "Depreciation": [sec_entry(d_and_a, "2025-12-31", 2025, start="2025-01-01")],
        "CommonStockSharesOutstanding": [sec_entry(shares, "2025-12-31", 2025)],
        "CashAndCashEquivalentsAtCarryingValue": [sec_entry(cash, "2025-12-31", 2025)],
        "LongTermDebtNoncurrent": [sec_entry(debt, "2025-12-31", 2025)],
    }
    return {"cik": 1, "entityName": "Test", **sec_facts(tags)}


def _multi_cik_sec_client(by_cik: dict) -> SECClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        cik = next(c for c in by_cik if f"CIK{c}" in url)
        submissions, facts = by_cik[cik]
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        return httpx.Response(200, json=facts)

    transport = httpx.MockTransport(handler)
    return SECClient(user_agent="test", min_interval=0, client=httpx.Client(transport=transport))


def _price_client(prices: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        ticker = str(request.url).split("/chart/")[1].split("?")[0]
        price = prices.get(ticker)
        if price is None:
            return httpx.Response(404, json={})
        body = {"chart": {"result": [{"meta": {"regularMarketPrice": price}}]}}
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_peers_for_sic_matches_known_prefix():
    prefix, peers = peers_for_sic("3674")
    assert prefix == "3674"
    assert "NVDA" in peers


def test_peers_for_sic_falls_back_to_empty_when_unmatched():
    _prefix, peers = peers_for_sic("9999")
    assert peers == []


@pytest.fixture
def peer_setup(tmp_path):
    target_facts = _facts(
        revenue=1_000_000_000, net_income=200_000_000, operating_income=300_000_000,
        d_and_a=50_000_000, shares=100_000_000, cash=50_000_000, debt=100_000_000,
    )
    peer_a_facts = _facts(
        revenue=2_000_000_000, net_income=400_000_000, operating_income=600_000_000,
        d_and_a=100_000_000, shares=200_000_000, cash=100_000_000, debt=200_000_000,
    )
    peer_b_facts = _facts(
        revenue=500_000_000, net_income=80_000_000, operating_income=120_000_000,
        d_and_a=20_000_000, shares=50_000_000, cash=25_000_000, debt=50_000_000,
    )

    sec_client = _multi_cik_sec_client(
        {
            "0000001000": (_submissions("TARG", TARGET_SIC), target_facts),
            "0000002000": (_submissions("PEERA", TARGET_SIC), peer_a_facts),
            "0000003000": (_submissions("PEERB", TARGET_SIC), peer_b_facts),
        }
    )
    ticker_map = build_ticker_map_with_cache(
        tmp_path, {"TARG": "1000", "PEERA": "2000", "PEERB": "3000"}
    )
    target_statement = max(
        normalize(target_facts, _submissions("TARG", TARGET_SIC)), key=lambda s: s.period_end
    )
    return sec_client, ticker_map, target_statement


def test_estimate_comps_computes_median_and_implied_value(monkeypatch, peer_setup):
    sec_client, ticker_map, target_statement = peer_setup
    monkeypatch.setattr(
        "app.valuation.comps.INDUSTRY_PEERS", [(TARGET_SIC, ["TARG", "PEERA", "PEERB"])]
    )
    price_client = _price_client({"PEERA": 30.0, "PEERB": 20.0})

    result = estimate_comps("TARG", target_statement, sec_client, ticker_map, price_client)

    assert {p.ticker for p in result.peers} == {"PEERA", "PEERB"}
    peer_a = next(p for p in result.peers if p.ticker == "PEERA")
    # PEERA: EV = 30*200M + 200M - 100M = 6,100M; EBITDA = 600M+100M=700M
    assert peer_a.ev_to_ebitda == pytest.approx(6_100_000_000 / 700_000_000)
    assert peer_a.ev_to_revenue == pytest.approx(6_100_000_000 / 2_000_000_000)
    assert peer_a.price_to_earnings == pytest.approx(30.0 / (400_000_000 / 200_000_000))

    # Median of 2 peers = average
    expected_median_ev_ebitda = (peer_a.ev_to_ebitda + next(
        p.ev_to_ebitda for p in result.peers if p.ticker == "PEERB"
    )) / 2
    assert result.median_ev_to_ebitda == pytest.approx(expected_median_ev_ebitda)

    # Implied value uses target's own EBITDA (300M+50M=350M), debt=100M, cash=50M, shares=100M
    target_ebitda = 350_000_000
    implied_ev = result.median_ev_to_ebitda * target_ebitda
    expected_implied = (implied_ev - 100_000_000 + 50_000_000) / 100_000_000
    assert result.implied_value_per_share_ebitda == pytest.approx(expected_implied)

    assert any("low-confidence sample" in w for w in result.warnings)


def test_estimate_comps_excludes_target_from_its_own_peer_list(monkeypatch, peer_setup):
    sec_client, ticker_map, target_statement = peer_setup
    monkeypatch.setattr(
        "app.valuation.comps.INDUSTRY_PEERS", [(TARGET_SIC, ["TARG", "PEERA", "PEERB"])]
    )
    price_client = _price_client({"PEERA": 30.0, "PEERB": 20.0})

    result = estimate_comps("TARG", target_statement, sec_client, ticker_map, price_client)

    assert "TARG" not in {p.ticker for p in result.peers}


def test_estimate_comps_skips_peer_that_fails_to_fetch(monkeypatch, peer_setup):
    sec_client, ticker_map, target_statement = peer_setup
    monkeypatch.setattr(
        "app.valuation.comps.INDUSTRY_PEERS", [(TARGET_SIC, ["TARG", "PEERA", "PEERB"])]
    )
    # PEERB's price is missing from this client -> that peer is skipped,
    # not a crash for the whole comps analysis.
    price_client = _price_client({"PEERA": 30.0})

    result = estimate_comps("TARG", target_statement, sec_client, ticker_map, price_client)

    assert {p.ticker for p in result.peers} == {"PEERA"}
    assert any("PEERB" in w for w in result.warnings)


def test_estimate_comps_no_curated_peers_returns_empty_with_warning(monkeypatch, peer_setup):
    sec_client, ticker_map, target_statement = peer_setup
    monkeypatch.setattr("app.valuation.comps.INDUSTRY_PEERS", [])
    price_client = _price_client({})

    result = estimate_comps("TARG", target_statement, sec_client, ticker_map, price_client)

    assert result.peers == []
    assert any("no curated peer list" in w for w in result.warnings)
