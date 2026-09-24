from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from threading import Lock
import time


def test_every_alpaca_data_request_uses_iex(monkeypatch):
    from services import alpaca_market_data as market
    from alpaca.data.enums import DataFeed

    requests = []
    from datetime import datetime, timezone

    bar = SimpleNamespace(
        close=100,
        open=99,
        high=101,
        low=98,
        volume=20,
        timestamp=datetime.now(timezone.utc),
    )

    class Data:
        def get_stock_bars(self, request):
            requests.append(request)
            return SimpleNamespace(data={"AAPL": [bar]})

        def get_stock_latest_trade(self, request):
            requests.append(request)
            return {"AAPL": SimpleNamespace(price=100)}

    client = object.__new__(market.AlpacaClient)
    client._data_client = Data()
    client._wait = lambda: None
    monkeypatch.setattr(market, "delta_t_minutes", lambda: 0)
    assert client.get_last_price("AAPL") == 100
    monkeypatch.setattr(market, "delta_t_minutes", lambda: 15)
    assert client.get_last_price("AAPL") == 100
    assert client.get_last_close_price("AAPL") == 100
    assert len(client.get_kline_data("AAPL", "1d", 30)) == 1
    assert len(requests) == 4
    assert all(request.feed == DataFeed.IEX for request in requests)


def test_concurrent_price_cache_misses_share_one_provider_request(monkeypatch):
    from services import market_data
    from services.price_cache import PriceCache
    from benchmark.providers import PriceResult, Freshness
    from services.time_source import now_utc

    calls = []

    class Port:
        def get_price(self, symbol, market):
            calls.append(symbol)
            time.sleep(0.03)
            return PriceResult(
                value=Decimal("100"),
                as_of=now_utc(),
                source="test",
                freshness=Freshness.FRESH,
            )

    monkeypatch.setattr(market_data, "price_cache", PriceCache())
    monkeypatch.setattr(market_data, "_create_market_data_port", Port)
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(
            executor.map(
                lambda _: market_data.get_price_result("BTC", allow_stale=False),
                range(8),
            )
        )
    assert len(values) == 8
    assert calls == ["BTC"]


def test_shared_ccxt_request_session_is_serialized():
    from services.hyperliquid_market_data import HyperliquidClient
    from threading import RLock

    client = object.__new__(HyperliquidClient)
    client._request_lock = RLock()
    active = peak = 0
    guard = Lock()

    def request():
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.01)
        with guard:
            active -= 1
        return 100

    client.exchange = SimpleNamespace(fetch_ticker=request)
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _: client._request("fetch_ticker"), range(8)))
    assert values == [100] * 8
    assert peak == 1


def test_alpaca_sdk_transport_has_bounded_timeout(monkeypatch):
    from requests.adapters import HTTPAdapter
    from services.alpaca_market_data import _BoundedHTTPAdapter

    seen = []
    monkeypatch.setattr(
        HTTPAdapter,
        "send",
        lambda self, request, **kwargs: seen.append(kwargs["timeout"]),
    )
    transport = _BoundedHTTPAdapter()
    transport.send(object(), timeout=None)
    transport.send(object(), timeout=2)
    assert seen == [(3, 15), 2]


def test_crypto_ticker_requests_do_not_enumerate_unrelated_markets():
    from services.hyperliquid_market_data import HyperliquidClient
    from threading import RLock

    client = object.__new__(HyperliquidClient)
    client._request_lock = RLock()
    calls = []

    def ticker(symbol, params):
        calls.append((symbol, params))
        return {"last": 100}

    client.exchange = SimpleNamespace(fetch_ticker=ticker)
    assert client.get_last_price("BTC") == 100
    assert client.get_last_price("HYPE") == 100
    assert calls == [
        ("BTC/USDC:USDC", {"type": "swap"}),
        ("HYPE/USDC", {"type": "spot"}),
    ]


def test_pending_order_scan_stops_after_current_order_on_shutdown(monkeypatch):
    from services import order_matching, scheduler

    stopped = False
    processed = []
    monkeypatch.setattr(order_matching, "get_pending_orders", lambda db: [1, 2, 3])
    monkeypatch.setattr(scheduler, "shutdown_cancellation_requested", lambda: stopped)

    def execute(db, order):
        nonlocal stopped
        processed.append(order)
        stopped = True
        return True

    monkeypatch.setattr(order_matching, "check_and_execute_order", execute)
    assert order_matching.process_all_pending_orders(object()) == (1, 1)
    assert processed == [1]
