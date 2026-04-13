from services.asset_curve_cache_service import get_curve_point_limit


def test_asset_curve_point_limit_by_timeframe():
    assert get_curve_point_limit("5m") == 20
    assert get_curve_point_limit("1h") == 20
    assert get_curve_point_limit("1d") == 31
