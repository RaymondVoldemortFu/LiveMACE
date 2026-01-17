"""
Initialize Asset Metadata - Populate asset_metadata table with known cryptocurrencies
Run this script once to initialize the metadata for supported symbols
"""
import sys
import os

# Add backend directory to path
sys.path.insert(0, os.path.dirname(__file__))

from database.connection import SessionLocal, engine, Base
from database.models import AssetMetadata
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Asset metadata definitions
# Based on common crypto classifications as of 2024-2026
ASSET_METADATA = [
    {
        "symbol": "BTC",
        "sector": "Layer1",
        "is_meme": "false",
        "market_cap_usd": None,  # Can be updated later
        "instrument_type": "spot",
        "liquidity_score": 1.0,  # Highest liquidity
    },
    {
        "symbol": "ETH",
        "sector": "Layer1",
        "is_meme": "false",
        "market_cap_usd": None,
        "instrument_type": "spot",
        "liquidity_score": 1.0,
    },
    {
        "symbol": "SOL",
        "sector": "Layer1",
        "is_meme": "false",
        "market_cap_usd": None,
        "instrument_type": "spot",
        "liquidity_score": 0.9,
    },
    {
        "symbol": "BNB",
        "sector": "Exchange",
        "is_meme": "false",
        "market_cap_usd": None,
        "instrument_type": "spot",
        "liquidity_score": 0.95,
    },
    {
        "symbol": "XRP",
        "sector": "Layer1",
        "is_meme": "false",
        "market_cap_usd": None,
        "instrument_type": "spot",
        "liquidity_score": 0.85,
    },
    {
        "symbol": "DOGE",
        "sector": "Meme",
        "is_meme": "true",
        "market_cap_usd": None,
        "instrument_type": "spot",
        "liquidity_score": 0.8,
    },
]


def init_asset_metadata():
    """Initialize or update asset metadata in database"""
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        for asset_data in ASSET_METADATA:
            # Check if asset already exists
            existing = db.query(AssetMetadata).filter(
                AssetMetadata.symbol == asset_data["symbol"]
            ).first()
            
            if existing:
                # Update existing
                for key, value in asset_data.items():
                    if value is not None:
                        setattr(existing, key, value)
                logger.info(f"Updated metadata for {asset_data['symbol']}")
            else:
                # Create new
                metadata = AssetMetadata(**asset_data)
                db.add(metadata)
                logger.info(f"Created metadata for {asset_data['symbol']}")
        
        db.commit()
        logger.info(f"Successfully initialized {len(ASSET_METADATA)} asset metadata records")
        
    except Exception as e:
        logger.error(f"Failed to initialize asset metadata: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("Starting asset metadata initialization...")
    init_asset_metadata()
    logger.info("Asset metadata initialization complete")
