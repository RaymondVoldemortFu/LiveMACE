from services.asset_curve_cache_service import get_curve_point_limit, should_backfill_recent_1h


def test_asset_curve_point_limit_by_timeframe():
    assert get_curve_point_limit("5m") == 20
    assert get_curve_point_limit("1h") == 20
    assert get_curve_point_limit("1d") == 31


def test_should_backfill_recent_1h():
    now_ts = 2_000_000_000
    assert should_backfill_recent_1h(None, now_ts) is True
    assert should_backfill_recent_1h(now_ts - 3599, now_ts) is False
    assert should_backfill_recent_1h(now_ts - 3600, now_ts) is False
    assert should_backfill_recent_1h(now_ts - 3601, now_ts) is True
