from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy.orm import Session

from database.models import MarketKline
from factors import compute_all_factors, compute_selected_factors, list_factors


class RankingApiService:
    def __init__(self, db: Optional[Session] = None):
        self.db = db

    @staticmethod
    def available_factors():
        factors = list_factors()
        all_columns = [column for factor in factors for column in factor.columns]
        all_columns.append(
            {"key": "Composite Score", "label": "Composite Score", "type": "score", "sortable": True}
        )
        return {
            "success": True,
            "factors": [
                {
                    "id": factor.id,
                    "name": factor.name,
                    "description": factor.description,
                    "columns": factor.columns,
                }
                for factor in factors
            ],
            "all_columns": all_columns,
        }

    @staticmethod
    def _date_range(days: int):
        end_date = datetime.now().date()
        return end_date - timedelta(days=days), end_date

    def ranking_table(self, days: int, factors: Optional[str], limit: int):
        start_date, end_date = self._date_range(days)
        rows = (
            self.db.query(MarketKline)
            .filter(
                MarketKline.period == "1d",
                MarketKline.datetime_str >= start_date.strftime("%Y-%m-%d"),
                MarketKline.datetime_str <= end_date.strftime("%Y-%m-%d"),
            )
            .order_by(MarketKline.symbol, MarketKline.timestamp)
            .all()
        )
        if not rows:
            return {"success": True, "data": [], "message": "No K-line data found for the specified period"}
        history = {}
        for kline in rows:
            history.setdefault(kline.symbol, []).append(
                {
                    "Date": kline.datetime_str,
                    "Open": float(kline.open_price) if kline.open_price else 0,
                    "High": float(kline.high_price) if kline.high_price else 0,
                    "Low": float(kline.low_price) if kline.low_price else 0,
                    "Close": float(kline.close_price) if kline.close_price else 0,
                    "Volume": float(kline.volume) if kline.volume else 0,
                    "Amount": float(kline.amount) if kline.amount else 0,
                }
            )
        history_dfs = {}
        for symbol, data in history.items():
            if len(data) >= 10:
                frame = pd.DataFrame(data)
                frame["Date"] = pd.to_datetime(frame["Date"], format="mixed")
                history_dfs[symbol] = frame.sort_values("Date")
        if not history_dfs:
            return {"success": True, "data": [], "message": "Insufficient data for factor calculation"}
        factor_ids = [factor.strip() for factor in factors.split(",")] if factors else None
        result_df = (
            compute_selected_factors(history_dfs, None, factor_ids)
            if factor_ids
            else compute_all_factors(history_dfs, None)
        )
        if result_df.empty:
            return {"success": True, "data": [], "message": "No factor results computed"}
        score_columns = [column for column in result_df.columns if "score" in column.lower()]
        if score_columns:
            result_df["Composite Score"] = result_df[score_columns].mean(axis=1, skipna=True)
            result_df = result_df.sort_values("Composite Score", ascending=False, na_position="last")
        result_data = result_df.head(limit).to_dict("records")
        for row in result_data:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None
        return {
            "success": True,
            "data": result_data,
            "total_symbols": len(history_dfs),
            "data_period": f"{start_date} to {end_date}",
            "factors_computed": factor_ids if factor_ids else "all",
        }

    def available_symbols(self, days: int):
        start_date, end_date = self._date_range(days)
        query = (
            self.db.query(MarketKline.symbol)
            .filter(
                MarketKline.period == "1d",
                MarketKline.datetime_str >= start_date.strftime("%Y-%m-%d"),
                MarketKline.datetime_str <= end_date.strftime("%Y-%m-%d"),
            )
            .distinct()
        )
        symbols = [row.symbol for row in query.all()]
        return {
            "success": True,
            "symbols": symbols,
            "count": len(symbols),
            "data_period": f"{start_date} to {end_date}",
        }
