"""
Asset Configuration - Centralized crypto sector classification
"""

# Built-in crypto sector classification (ground truth, independent of asset_metadata table)
# Based on actual blockchain characteristics and use cases
# Used by: rule_validator.py, rule_evaluator.py, metrics_calculator.py
CRYPTO_SECTOR_MAP = {
    # Layer1 - Smart Contract Platforms (preferred for growth potential)
    "ETH": "SmartContract",    # Ethereum - leading smart contract platform
    "SOL": "SmartContract",    # Solana - high-performance smart contract platform
    
    # Layer1 - Value Storage (conservative infrastructure)
    "BTC": "ValueStore",       # Bitcoin - digital gold, store of value
    
    # Exchange Tokens
    "BNB": "Exchange",         # Binance Coin - exchange utility token
    
    # Additional coins (for future expansion)
    "AAVE": "DeFi",
    "UNI": "DeFi",
    "NEWTOKEN": "DeFi",
    "ARB": "Layer2",
    "MATIC": "Layer2",
    "OP": "Layer2",
    "AVAX": "SmartContract",
    "XRP": "Payment",
    "DOGE": "Meme",
    "SHIB": "Meme",
    "LINK": "Oracle",
}
