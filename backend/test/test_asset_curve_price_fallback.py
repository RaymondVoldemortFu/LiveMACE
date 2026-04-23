from services.asset_curve_calculator import _lookup_price_with_last_close


def test_lookup_price_with_last_close_exact_and_previous():
    price_map = {
        100: 10.0,
        200: 20.0,
        350: 35.0,
    }

    assert _lookup_price_with_last_close(price_map, 200) == 20.0
    assert _lookup_price_with_last_close(price_map, 260) == 20.0
    assert _lookup_price_with_last_close(price_map, 1000) == 35.0


def test_lookup_price_with_last_close_returns_none_when_no_earlier_price():
    price_map = {
        100: 10.0,
        200: 20.0,
    }

    assert _lookup_price_with_last_close(price_map, 99) is None
    assert _lookup_price_with_last_close({}, 200) is None
