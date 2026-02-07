import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from database.connection import engine


def rename_crypto_klines_table():
    print("Renaming crypto_klines to market_klines if needed...")
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='market_klines'"))
            if result.fetchone():
                print("market_klines already exists. No rename needed.")
                return

            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='crypto_klines'"))
            if not result.fetchone():
                print("crypto_klines does not exist. No rename performed.")
                return

            conn.execute(text("ALTER TABLE crypto_klines RENAME TO market_klines"))
            print("Renamed crypto_klines to market_klines.")
        except Exception as e:
            print(f"Failed to rename table: {e}")


if __name__ == "__main__":
    rename_crypto_klines_table()
