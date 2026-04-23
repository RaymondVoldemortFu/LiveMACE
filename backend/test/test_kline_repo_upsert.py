import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database.connection import Base
from database.models import MarketKline
from repositories.kline_repo import KlineRepository


def _new_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return Session()


def test_save_kline_data_upsert_same_key_updates_without_duplicates():
    db = _new_session()
    repo = KlineRepository(db)
    try:
        first = [{
            "timestamp": 1776093900,
            "datetime_str": "2026-04-13T15:25:00+00:00",
            "open": 602.12,
            "high": 602.76,
            "low": 601.80,
            "close": 602.29,
            "volume": 51.34,
            "amount": 30921.56,
            "change": 0.17,
            "percent": 0.0282,
        }]
        second = [{
            "timestamp": 1776093900,
            "datetime_str": "2026-04-13T15:25:00+00:00",
            "open": 602.12,
            "high": 603.00,
            "low": 601.80,
            "close": 603.11,
            "volume": 52.00,
            "amount": 31361.72,
            "change": 0.99,
            "percent": 0.1644,
        }]

        result_1 = repo.save_kline_data("BNB", "CRYPTO", "5m", first)
        result_2 = repo.save_kline_data("BNB", "CRYPTO", "5m", second)

        rows = db.query(MarketKline).filter(
            MarketKline.symbol == "BNB",
            MarketKline.market == "CRYPTO",
            MarketKline.period == "5m",
            MarketKline.timestamp == 1776093900,
        ).all()
        assert len(rows) == 1
        assert float(rows[0].close_price) == 603.11
        assert result_1["inserted"] == 1
        assert result_2["updated"] == 1
    finally:
        db.close()


def test_save_kline_data_deduplicates_same_payload_timestamp():
    db = _new_session()
    repo = KlineRepository(db)
    try:
        payload = [
            {
                "timestamp": 1,
                "datetime_str": "1970-01-01T00:00:01+00:00",
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
            },
            {
                "timestamp": 1,
                "datetime_str": "1970-01-01T00:00:01+00:00",
                "open": 2,
                "high": 2,
                "low": 2,
                "close": 2,
            },
        ]
        result = repo.save_kline_data("BTC", "CRYPTO", "1m", payload)
        rows = db.query(MarketKline).all()
        assert len(rows) == 1
        assert float(rows[0].close_price) == 2.0
        assert result["total"] == 1
    finally:
        db.close()
