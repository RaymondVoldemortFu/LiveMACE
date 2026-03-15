"""
Cryptocurrency Classification System
Automatically classify cryptocurrencies by sector and meme status
"""
from typing import Dict, Literal, Optional
import re

# Type definitions
SectorType = Literal["Layer1", "Layer2", "DeFi", "Exchange", "Meme", "GameFi", "AI", "Privacy", "Oracle", "Storage", "Unknown"]

class CryptoClassifier:
    """
    Classify cryptocurrencies based on symbol patterns and known databases
    
    Classification methodology:
    1. Explicit mappings for well-known cryptos
    2. Pattern-based detection for meme coins
    3. Symbol-based heuristics for sectors
    """
    
    # Well-known cryptocurrency classifications
    KNOWN_CLASSIFICATIONS: Dict[str, Dict[str, any]] = {
        # Layer 1 Blockchains
        "BTC": {"sector": "Layer1", "is_meme": False, "full_name": "Bitcoin"},
        "ETH": {"sector": "Layer1", "is_meme": False, "full_name": "Ethereum"},
        "SOL": {"sector": "Layer1", "is_meme": False, "full_name": "Solana"},
        "ADA": {"sector": "Layer1", "is_meme": False, "full_name": "Cardano"},
        "AVAX": {"sector": "Layer1", "is_meme": False, "full_name": "Avalanche"},
        "DOT": {"sector": "Layer1", "is_meme": False, "full_name": "Polkadot"},
        "ATOM": {"sector": "Layer1", "is_meme": False, "full_name": "Cosmos"},
        "NEAR": {"sector": "Layer1", "is_meme": False, "full_name": "Near Protocol"},
        "APT": {"sector": "Layer1", "is_meme": False, "full_name": "Aptos"},
        "SUI": {"sector": "Layer1", "is_meme": False, "full_name": "Sui"},
        "XRP": {"sector": "Layer1", "is_meme": False, "full_name": "Ripple"},
        "TRX": {"sector": "Layer1", "is_meme": False, "full_name": "Tron"},
        "TON": {"sector": "Layer1", "is_meme": False, "full_name": "Toncoin"},
        "ALGO": {"sector": "Layer1", "is_meme": False, "full_name": "Algorand"},
        "FTM": {"sector": "Layer1", "is_meme": False, "full_name": "Fantom"},
        
        # Layer 2 Solutions
        "ARB": {"sector": "Layer2", "is_meme": False, "full_name": "Arbitrum"},
        "OP": {"sector": "Layer2", "is_meme": False, "full_name": "Optimism"},
        "MATIC": {"sector": "Layer2", "is_meme": False, "full_name": "Polygon"},
        "POLY": {"sector": "Layer2", "is_meme": False, "full_name": "Polygon"},
        "IMX": {"sector": "Layer2", "is_meme": False, "full_name": "Immutable X"},
        "METIS": {"sector": "Layer2", "is_meme": False, "full_name": "Metis"},
        "STRK": {"sector": "Layer2", "is_meme": False, "full_name": "Starknet"},
        
        # Exchange Tokens
        "BNB": {"sector": "Exchange", "is_meme": False, "full_name": "Binance Coin"},
        "OKB": {"sector": "Exchange", "is_meme": False, "full_name": "OKB"},
        "CRO": {"sector": "Exchange", "is_meme": False, "full_name": "Cronos"},
        "FTT": {"sector": "Exchange", "is_meme": False, "full_name": "FTX Token"},
        "UNI": {"sector": "DeFi", "is_meme": False, "full_name": "Uniswap"},
        "HT": {"sector": "Exchange", "is_meme": False, "full_name": "Huobi Token"},
        "KCS": {"sector": "Exchange", "is_meme": False, "full_name": "KuCoin Token"},
        "GT": {"sector": "Exchange", "is_meme": False, "full_name": "GateToken"},
        
        # DeFi Tokens
        "AAVE": {"sector": "DeFi", "is_meme": False, "full_name": "Aave"},
        "MKR": {"sector": "DeFi", "is_meme": False, "full_name": "Maker"},
        "SNX": {"sector": "DeFi", "is_meme": False, "full_name": "Synthetix"},
        "CRV": {"sector": "DeFi", "is_meme": False, "full_name": "Curve"},
        "COMP": {"sector": "DeFi", "is_meme": False, "full_name": "Compound"},
        "SUSHI": {"sector": "DeFi", "is_meme": False, "full_name": "SushiSwap"},
        "YFI": {"sector": "DeFi", "is_meme": False, "full_name": "Yearn Finance"},
        "BAL": {"sector": "DeFi", "is_meme": False, "full_name": "Balancer"},
        "LDO": {"sector": "DeFi", "is_meme": False, "full_name": "Lido DAO"},
        "PENDLE": {"sector": "DeFi", "is_meme": False, "full_name": "Pendle"},
        "GMX": {"sector": "DeFi", "is_meme": False, "full_name": "GMX"},
        
        # Meme Coins (Pattern-based + explicit)
        "DOGE": {"sector": "Meme", "is_meme": True, "full_name": "Dogecoin"},
        "SHIB": {"sector": "Meme", "is_meme": True, "full_name": "Shiba Inu"},
        "PEPE": {"sector": "Meme", "is_meme": True, "full_name": "Pepe"},
        "FLOKI": {"sector": "Meme", "is_meme": True, "full_name": "Floki"},
        "BONK": {"sector": "Meme", "is_meme": True, "full_name": "Bonk"},
        "WIF": {"sector": "Meme", "is_meme": True, "full_name": "dogwifhat"},
        "MEW": {"sector": "Meme", "is_meme": True, "full_name": "Cat in a Dog's World"},
        "WOJAK": {"sector": "Meme", "is_meme": True, "full_name": "Wojak"},
        "SMOG": {"sector": "Meme", "is_meme": True, "full_name": "Smog"},
        "TURBO": {"sector": "Meme", "is_meme": True, "full_name": "Turbo"},
        "BABYDOGE": {"sector": "Meme", "is_meme": True, "full_name": "Baby Doge"},
        "ELON": {"sector": "Meme", "is_meme": True, "full_name": "Dogelon Mars"},
        "SAMO": {"sector": "Meme", "is_meme": True, "full_name": "Samoyedcoin"},
        
        # Gaming / Metaverse
        "AXS": {"sector": "GameFi", "is_meme": False, "full_name": "Axie Infinity"},
        "SAND": {"sector": "GameFi", "is_meme": False, "full_name": "The Sandbox"},
        "MANA": {"sector": "GameFi", "is_meme": False, "full_name": "Decentraland"},
        "GALA": {"sector": "GameFi", "is_meme": False, "full_name": "Gala"},
        "ENJ": {"sector": "GameFi", "is_meme": False, "full_name": "Enjin Coin"},
        "RONIN": {"sector": "GameFi", "is_meme": False, "full_name": "Ronin"},
        "BEAM": {"sector": "GameFi", "is_meme": False, "full_name": "Beam"},
        "PRIME": {"sector": "GameFi", "is_meme": False, "full_name": "Echelon Prime"},
        
        # AI / ML Tokens
        "FET": {"sector": "AI", "is_meme": False, "full_name": "Fetch.ai"},
        "AGIX": {"sector": "AI", "is_meme": False, "full_name": "SingularityNET"},
        "OCEAN": {"sector": "AI", "is_meme": False, "full_name": "Ocean Protocol"},
        "RNDR": {"sector": "AI", "is_meme": False, "full_name": "Render"},
        "GRT": {"sector": "AI", "is_meme": False, "full_name": "The Graph"},
        "WLD": {"sector": "AI", "is_meme": False, "full_name": "Worldcoin"},
        "TAO": {"sector": "AI", "is_meme": False, "full_name": "Bittensor"},
        
        # Privacy Coins
        "XMR": {"sector": "Privacy", "is_meme": False, "full_name": "Monero"},
        "ZEC": {"sector": "Privacy", "is_meme": False, "full_name": "Zcash"},
        "DASH": {"sector": "Privacy", "is_meme": False, "full_name": "Dash"},
        "SECRET": {"sector": "Privacy", "is_meme": False, "full_name": "Secret Network"},
        
        # Oracle Networks
        "LINK": {"sector": "Oracle", "is_meme": False, "full_name": "Chainlink"},
        "BAND": {"sector": "Oracle", "is_meme": False, "full_name": "Band Protocol"},
        "TRB": {"sector": "Oracle", "is_meme": False, "full_name": "Tellor"},
        "API3": {"sector": "Oracle", "is_meme": False, "full_name": "API3"},
        
        # Storage / Infrastructure
        "FIL": {"sector": "Storage", "is_meme": False, "full_name": "Filecoin"},
        "AR": {"sector": "Storage", "is_meme": False, "full_name": "Arweave"},
        "STORJ": {"sector": "Storage", "is_meme": False, "full_name": "Storj"},
        "HNT": {"sector": "Storage", "is_meme": False, "full_name": "Helium"},
    }
    
    # Meme coin detection patterns
    MEME_PATTERNS = [
        r".*INU$",      # Shiba Inu, Baby Inu, etc.
        r".*DOGE.*",    # Any doge variation
        r".*PEPE.*",    # Pepe variations
        r".*BABY.*",    # Baby tokens
        r".*MOON.*",    # Moon tokens (often memes)
        r".*SAFE.*",    # SafeMoon style
        r".*ELON.*",    # Elon-related
        r".*CAT$",      # Cat tokens
        r".*DOG$",      # Dog tokens
        r".*SHIB.*",    # Shib variations
        r".*WOJAK.*",   # Wojak meme
        r".*TURBO.*",   # Turbo tokens
        r".*CHAD.*",    # Chad tokens
        r"^(WIF|MEW|BONK|FLOKI)$",  # Known meme tickers
    ]
    
    def __init__(self):
        """Initialize the classifier with pattern matchers"""
        self.meme_pattern = re.compile("|".join(self.MEME_PATTERNS), re.IGNORECASE)
    
    def classify(self, symbol: str) -> Dict[str, any]:
        """
        Classify a cryptocurrency symbol
        
        Args:
            symbol: Trading symbol (e.g., "BTC", "DOGE", "ETH")
        
        Returns:
            Dict with keys: sector, is_meme, full_name, confidence
        """
        symbol_clean = self._clean_symbol(symbol)
        
        # Check explicit mappings first (highest confidence)
        if symbol_clean in self.KNOWN_CLASSIFICATIONS:
            result = self.KNOWN_CLASSIFICATIONS[symbol_clean].copy()
            result["confidence"] = "high"
            return result
        
        # Pattern-based meme detection (medium confidence)
        if self.meme_pattern.match(symbol_clean):
            return {
                "sector": "Meme",
                "is_meme": True,
                "full_name": symbol_clean,
                "confidence": "medium"
            }
        
        # Unknown - use conservative defaults (low confidence)
        return {
            "sector": "Unknown",
            "is_meme": False,
            "full_name": symbol_clean,
            "confidence": "low"
        }
    
    def _clean_symbol(self, symbol: str) -> str:
        """
        Clean and normalize symbol
        
        Examples:
            BTC/USDC:USDC -> BTC
            ETH/USD -> ETH
            DOGE -> DOGE
        """
        # Remove trading pair suffixes
        if "/" in symbol:
            symbol = symbol.split("/")[0]
        if ":" in symbol:
            symbol = symbol.split(":")[0]
        
        return symbol.upper().strip()
    
    def get_all_known_symbols(self) -> list[str]:
        """Return list of all symbols with known classifications"""
        return list(self.KNOWN_CLASSIFICATIONS.keys())
    
    def get_sector_symbols(self, sector: SectorType) -> list[str]:
        """Get all symbols for a specific sector"""
        return [
            symbol for symbol, info in self.KNOWN_CLASSIFICATIONS.items()
            if info["sector"] == sector
        ]
    
    def get_meme_coins(self) -> list[str]:
        """Get all known meme coins"""
        return [
            symbol for symbol, info in self.KNOWN_CLASSIFICATIONS.items()
            if info["is_meme"]
        ]
    
    def add_custom_classification(
        self, 
        symbol: str, 
        sector: SectorType, 
        is_meme: bool, 
        full_name: Optional[str] = None
    ):
        """
        Add or update a custom classification
        
        Useful for manually adding new tokens or overriding defaults
        """
        symbol_clean = self._clean_symbol(symbol)
        self.KNOWN_CLASSIFICATIONS[symbol_clean] = {
            "sector": sector,
            "is_meme": is_meme,
            "full_name": full_name or symbol_clean
        }


# Global classifier instance
crypto_classifier = CryptoClassifier()


def classify_crypto(symbol: str) -> Dict[str, any]:
    """
    Convenience function to classify a crypto symbol
    
    Usage:
        result = classify_crypto("DOGE")
        print(result)  # {"sector": "Meme", "is_meme": True, ...}
    """
    return crypto_classifier.classify(symbol)


if __name__ == "__main__":
    # Test the classifier
    test_symbols = [
        "BTC", "ETH", "DOGE", "SHIB", "PEPE", "LINK", 
        "UNI", "AAVE", "BNB", "ARB", "BABYINU", "NEWTOKEN",
        "BTC/USDC:USDC", "ETH/USD"
    ]
    
    print("=" * 80)
    print("Cryptocurrency Classification Test")
    print("=" * 80)
    
    for symbol in test_symbols:
        result = classify_crypto(symbol)
        confidence_emoji = {
            "high": "✅",
            "medium": "⚠️ ",
            "low": "❓"
        }
        emoji = confidence_emoji.get(result.get("confidence", "low"), "❓")
        
        print(f"\n{emoji} {symbol:20s} -> {result['sector']:12s} | "
              f"Meme: {str(result['is_meme']):5s} | "
              f"Confidence: {result.get('confidence', 'N/A')}")
    
    print("\n" + "=" * 80)
    print(f"Total known classifications: {len(crypto_classifier.get_all_known_symbols())}")
    print(f"Known meme coins: {len(crypto_classifier.get_meme_coins())}")
    print("=" * 80)
