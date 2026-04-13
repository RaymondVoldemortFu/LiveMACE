"""
K-line data repository module
Provides K-line data database operations
"""

from sqlalchemy.orm import Session
from sqlalchemy import and_, tuple_
from typing import Dict, List
from database.models import MarketKline


class KlineRepository:
    def __init__(self, db: Session):
        self.db = db

    def save_kline_data(self, symbol: str, market: str, period: str, kline_data: List[dict]) -> dict:
        """
        Save K-line data to database (using upsert mode)

        Args:
            symbol: Stock symbol
            market: Market symbol
            period: Time period
            kline_data: K-line data list

        Returns:
            Save result dict, contains inserted and updated counts
        """
        # Normalize rows and de-duplicate by (symbol, market, period, timestamp)
        # so upstream duplicate candles in one payload won't trigger self-conflicts.
        rows_by_key: Dict[tuple, dict] = {}
        for item in kline_data:
            timestamp = item.get("timestamp")
            if timestamp is None:
                continue

            row = {
                "symbol": symbol,
                "market": market,
                "period": period,
                "timestamp": int(timestamp),
                "datetime_str": item.get("datetime_str") or item.get("datetime", ""),
                "open_price": item.get("open"),
                "high_price": item.get("high"),
                "low_price": item.get("low"),
                "close_price": item.get("close"),
                "volume": item.get("volume"),
                "amount": item.get("amount"),
                "change": item.get("change") if item.get("change") is not None else item.get("chg"),
                "percent": item.get("percent"),
            }
            key = (row["symbol"], row["market"], row["period"], row["timestamp"])
            rows_by_key[key] = row

        rows = list(rows_by_key.values())
        if not rows:
            return {"inserted": 0, "updated": 0, "total": 0}

        keys = list(rows_by_key.keys())
        existing_rows = (
            self.db.query(
                MarketKline.symbol,
                MarketKline.market,
                MarketKline.period,
                MarketKline.timestamp,
            )
            .filter(
                tuple_(
                    MarketKline.symbol,
                    MarketKline.market,
                    MarketKline.period,
                    MarketKline.timestamp,
                ).in_(keys)
            )
            .all()
        )
        existing_keys = {(r[0], r[1], r[2], int(r[3])) for r in existing_rows}
        updated_count = len(existing_keys)
        inserted_count = len(keys) - updated_count

        dialect_name = self.db.bind.dialect.name if self.db.bind is not None else ""
        update_cols = {
            "datetime_str": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "close_price": None,
            "volume": None,
            "amount": None,
            "change": None,
            "percent": None,
        }

        if dialect_name == "mysql":
            from sqlalchemy.dialects.mysql import insert as mysql_insert

            stmt = mysql_insert(MarketKline).values(rows)
            stmt = stmt.on_duplicate_key_update(
                datetime_str=stmt.inserted.datetime_str,
                open_price=stmt.inserted.open_price,
                high_price=stmt.inserted.high_price,
                low_price=stmt.inserted.low_price,
                close_price=stmt.inserted.close_price,
                volume=stmt.inserted.volume,
                amount=stmt.inserted.amount,
                change=stmt.inserted.change,
                percent=stmt.inserted.percent,
            )
            self.db.execute(stmt)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(MarketKline).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "market", "period", "timestamp"],
                set_={
                    "datetime_str": stmt.excluded.datetime_str,
                    "open_price": stmt.excluded.open_price,
                    "high_price": stmt.excluded.high_price,
                    "low_price": stmt.excluded.low_price,
                    "close_price": stmt.excluded.close_price,
                    "volume": stmt.excluded.volume,
                    "amount": stmt.excluded.amount,
                    "change": stmt.excluded.change,
                    "percent": stmt.excluded.percent,
                },
            )
            self.db.execute(stmt)
        else:
            # Generic fallback: update-first then insert if missing.
            for row in rows:
                existing = self.db.query(MarketKline).filter(
                    and_(
                        MarketKline.symbol == row["symbol"],
                        MarketKline.market == row["market"],
                        MarketKline.period == row["period"],
                        MarketKline.timestamp == row["timestamp"],
                    )
                ).first()
                if existing:
                    for col in update_cols:
                        setattr(existing, col, row[col])
                else:
                    self.db.add(MarketKline(**row))

        self.db.commit()

        return {
            "inserted": inserted_count,
            "updated": updated_count,
            "total": len(rows),
        }

    def get_kline_data(self, symbol: str, market: str, period: str, limit: int = 100) -> List[MarketKline]:
        """
        Get K-line data

        Args:
            symbol: Stock symbol
            market: Market symbol
            period: Time period
            limit: Limit count

        Returns:
            K-line data list
        """
        return self.db.query(MarketKline).filter(
            and_(
                MarketKline.symbol == symbol,
                MarketKline.market == market,
                MarketKline.period == period
            )
        ).order_by(MarketKline.timestamp.desc()).limit(limit).all()

    def delete_old_kline_data(self, symbol: str, market: str, period: str, keep_days: int = 30):
        """
        Delete old K-line data

        Args:
            symbol: Stock symbol
            market: Market symbol
            period: Time period
            keep_days: Days to keep
        """
        import time
        cutoff_timestamp = int(time.time() - keep_days * 24 * 3600)
        
        self.db.query(MarketKline).filter(
            and_(
                MarketKline.symbol == symbol,
                MarketKline.market == market,
                MarketKline.period == period,
                MarketKline.timestamp < cutoff_timestamp
            )
        ).delete()
        
        self.db.commit()