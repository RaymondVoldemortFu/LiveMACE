"""
Initialize Asset Metadata - Populate asset_metadata table with cryptocurrencies
Automatically classifies cryptocurrencies using the CryptoClassifier

Features:
- Automatic sector classification (Layer1, DeFi, Meme, etc.)
- Meme coin detection (pattern-based + explicit list)
- Support for dynamic symbol lists from trading system
- Fallback to intelligent heuristics for unknown tokens
"""
import sys
import os

# Add backend directory to path
sys.path.insert(0, os.path.dirname(__file__))

from database.connection import SessionLocal, engine, Base
from database.models import AssetMetadata
from crypto_classification import crypto_classifier, classify_crypto
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_symbols_to_initialize():
    """
    Get list of symbols to initialize in database
    
    Priority sources (in order):
    1. AI_TRADING_SYMBOLS from trading_commands.py
    2. Additional high-priority symbols from classifier
    3. User-specified custom list
    
    Returns:
        List of symbol strings
    """
    symbols = []
    
    # Try to import from trading system
    try:
        from services.trading_commands import AI_TRADING_SYMBOLS
        symbols.extend(AI_TRADING_SYMBOLS)
        logger.info(f"Loaded {len(AI_TRADING_SYMBOLS)} symbols from AI_TRADING_SYMBOLS")
    except ImportError:
        logger.warning("Could not import AI_TRADING_SYMBOLS, using defaults")
        symbols = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]
    
    # Add additional mainstream tokens not in AI_TRADING_SYMBOLS
    additional_mainstream = [
        "AVAX", "MATIC", "ARB", "OP", "LINK", "UNI", "AAVE"
    ]
    for symbol in additional_mainstream:
        if symbol not in symbols:
            symbols.append(symbol)
    
    logger.info(f"Total symbols to initialize: {len(symbols)}")
    return symbols


def create_asset_metadata_from_symbol(symbol: str, default_liquidity: float = 0.8) -> dict:
    """
    Create asset metadata dict from symbol using automatic classification
    
    Args:
        symbol: Crypto symbol (e.g., "BTC", "DOGE")
        default_liquidity: Default liquidity score for unknown tokens
    
    Returns:
        Dictionary with all required AssetMetadata fields
    """
    # Use classifier to get sector and meme status
    classification = classify_crypto(symbol)
    
    # Map liquidity scores based on sector and known tokens
    liquidity_map = {
        "BTC": 1.0,
        "ETH": 1.0,
        "BNB": 0.95,
        "SOL": 0.9,
        "XRP": 0.85,
        "DOGE": 0.8,
    }
    liquidity_score = liquidity_map.get(symbol, default_liquidity)
    
    # Adjust liquidity by sector if not in map
    if symbol not in liquidity_map:
        sector_liquidity = {
            "Layer1": 0.85,
            "Layer2": 0.75,
            "DeFi": 0.7,
            "Exchange": 0.8,
            "Meme": 0.6,
            "Unknown": 0.5,
        }
        liquidity_score = sector_liquidity.get(classification["sector"], 0.5)
    
    return {
        "symbol": symbol,
        "sector": classification["sector"],
        "is_meme": "true" if classification["is_meme"] else "false",
        "market_cap_usd": None,  # Can be updated later via API
        "instrument_type": "spot",
        "liquidity_score": liquidity_score,
    }


def init_asset_metadata(symbols: list[str] = None, update_existing: bool = True):
    """
    Initialize or update asset metadata in database
    
    Args:
        symbols: List of symbols to initialize (None = auto-detect from trading system)
        update_existing: If True, update existing records; if False, skip existing
    """
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    # Get symbols to initialize
    if symbols is None:
        symbols = get_symbols_to_initialize()
    
    db = SessionLocal()
    try:
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        for symbol in symbols:
            # Generate metadata using classifier
            asset_data = create_asset_metadata_from_symbol(symbol)
            
            # Check if asset already exists
            existing = db.query(AssetMetadata).filter(
                AssetMetadata.symbol == symbol
            ).first()
            
            if existing:
                if update_existing:
                    # Update existing record
                    for key, value in asset_data.items():
                        if value is not None:
                            setattr(existing, key, value)
                    logger.info(f"✓ Updated: {symbol:8s} -> {asset_data['sector']:12s} (meme={asset_data['is_meme']})")
                    updated_count += 1
                else:
                    logger.info(f"⊘ Skipped: {symbol:8s} (already exists)")
                    skipped_count += 1
            else:
                # Create new record
                metadata = AssetMetadata(**asset_data)
                db.add(metadata)
                logger.info(f"+ Created: {symbol:8s} -> {asset_data['sector']:12s} (meme={asset_data['is_meme']})")
                created_count += 1
        
        db.commit()
        
        logger.info("=" * 80)
        logger.info(f"Asset Metadata Initialization Complete")
        logger.info(f"  Created: {created_count}")
        logger.info(f"  Updated: {updated_count}")
        logger.info(f"  Skipped: {skipped_count}")
        logger.info(f"  Total:   {created_count + updated_count + skipped_count}")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"Failed to initialize asset metadata: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def add_custom_symbol(symbol: str, sector: str = None, is_meme: bool = None):
    """
    Manually add or update a single cryptocurrency
    
    Args:
        symbol: Crypto symbol (e.g., "NEWTOKEN")
        sector: Optional sector override (None = auto-detect)
        is_meme: Optional meme status override (None = auto-detect)
    
    Usage:
        # Auto-detect everything
        add_custom_symbol("PEPE")
        
        # Manual override
        add_custom_symbol("NEWTOKEN", sector="DeFi", is_meme=False)
    """
    db = SessionLocal()
    try:
        # Get automatic classification
        asset_data = create_asset_metadata_from_symbol(symbol)
        logger.debug(f"Auto-classification for {symbol}: {asset_data}")
        
        # Apply manual overrides (must be done before checking, for correct logging)
        if sector is not None:
            asset_data["sector"] = sector
            logger.debug(f"Sector override applied: {sector}")
        if is_meme is not None:
            asset_data["is_meme"] = "true" if is_meme else "false"
            logger.debug(f"is_meme override applied: {is_meme} -> {asset_data['is_meme']}")
        
        logger.debug(f"Final asset_data before DB operation: {asset_data}")
        
        # Check if exists
        existing = db.query(AssetMetadata).filter(
            AssetMetadata.symbol == symbol
        ).first()
        
        if existing:
            for key, value in asset_data.items():
                setattr(existing, key, value)
            logger.info(f"Updated {symbol}: {asset_data}")
        else:
            metadata = AssetMetadata(**asset_data)
            db.add(metadata)
            logger.info(f"Created {symbol}: {asset_data}")
        
        db.commit()
        logger.info(f"Successfully added/updated {symbol}")
        
    except Exception as e:
        logger.error(f"Failed to add {symbol}: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Initialize cryptocurrency asset metadata with automatic classification"
    )
    parser.add_argument(
        "--add",
        type=str,
        help="Add a single symbol (e.g., --add PEPE)"
    )
    parser.add_argument(
        "--sector",
        type=str,
        choices=["Layer1", "Layer2", "DeFi", "Exchange", "Meme", "GameFi", "AI", "Privacy", "Oracle", "Storage", "Unknown"],
        help="Override sector classification"
    )
    parser.add_argument(
        "--is-meme",
        type=lambda x: x.lower() in ['true', '1', 'yes'],
        default=None,
        metavar="true/false",
        help="Override meme coin status (true/false, or omit for auto-detection)"
    )
    parser.add_argument(
        "--no-update",
        action="store_true",
        help="Skip updating existing records"
    )
    
    args = parser.parse_args()
    
    if args.add:
        # Add single symbol
        logger.info(f"Adding custom symbol: {args.add}")
        add_custom_symbol(args.add, args.sector, args.is_meme)
    else:
        # Initialize all symbols
        logger.info("Starting asset metadata initialization...")
        init_asset_metadata(update_existing=not args.no_update)
        logger.info("Asset metadata initialization complete")
