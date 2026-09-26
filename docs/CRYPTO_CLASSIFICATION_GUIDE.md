# Automatic Cryptocurrency Classification System

## 📖 Overview

This system provides **automatic classification** of cryptocurrencies for the `asset_metadata` database table, eliminating the need for manual data entry.

### Features

✅ **Automatic sector classification** - 81+ well-known cryptocurrencies mapped to sectors:
- Layer1 (BTC, ETH, SOL, ADA, etc.)
- Layer2 (ARB, OP, MATIC, etc.)
- DeFi (AAVE, UNI, SUSHI, etc.)
- Exchange (BNB, OKB, CRO, etc.)
- Meme (DOGE, SHIB, PEPE, etc.)
- GameFi, AI, Privacy, Oracle, Storage

✅ **Pattern-based meme detection** - Automatically identifies meme coins by patterns:
- `*INU` (Shiba Inu, Baby Inu)
- `*DOGE*` (Dogecoin variations)
- `*PEPE*`, `*BABY*`, `*MOON*`, `*SAFE*`, `*ELON*`
- `*CAT`, `*DOG` suffixes

✅ **Intelligent fallback** - Unknown tokens default to:
- Sector: `"Unknown"`
- is_meme: `false` (conservative)
- Liquidity: 0.5 (medium-low)

✅ **Manual override support** - CLI arguments for custom classification

---

## 🚀 Quick Start

### 1. Initialize Asset Metadata (Auto-mode)

```bash
# Initialize all AI trading symbols + mainstream tokens
python init_asset_metadata.py

# Output:
# INFO: Total symbols to initialize: 13
# INFO: ✓ Updated: BTC      -> Layer1       (meme=false)
# INFO: ✓ Updated: ETH      -> Layer1       (meme=false)
# INFO: ✓ Updated: DOGE     -> Meme         (meme=true)
# INFO: + Created: AVAX     -> Layer1       (meme=false)
# ...
# INFO: Asset Metadata Initialization Complete
#   Created: 7
#   Updated: 6
#   Total: 13
```

### 2. Add Individual Cryptocurrency (Auto-detect)

```bash
# Auto-detect sector and meme status
python init_asset_metadata.py --add PEPE

# Output:
# INFO: Created PEPE: {'sector': 'Meme', 'is_meme': 'true', ...}
```

### 3. Add with Manual Override

```bash
# Override sector or meme status
python init_asset_metadata.py --add NEWTOKEN --sector DeFi --is-meme false

# Output:
# INFO: Created NEWTOKEN: {'sector': 'DeFi', 'is_meme': 'false', ...}
```

---

## 🔧 Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│             crypto_classification.py                        │
│  ┌────────────────────────────────────────────────────┐    │
│  │  CryptoClassifier                                   │    │
│  │  - KNOWN_CLASSIFICATIONS (81+ tokens)              │    │
│  │  - MEME_PATTERNS (regex-based detection)           │    │
│  │  - classify(symbol) -> {sector, is_meme, ...}     │    │
│  └────────────────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│             init_asset_metadata.py                          │
│  ┌────────────────────────────────────────────────────┐    │
│  │  get_symbols_to_initialize()                       │    │
│  │   1. AI_TRADING_SYMBOLS from trading_commands.py   │    │
│  │   2. Additional mainstream tokens                  │    │
│  │                                                      │    │
│  │  create_asset_metadata_from_symbol(symbol)         │    │
│  │   → classify_crypto(symbol)                        │    │
│  │   → map liquidity scores                           │    │
│  │   → return metadata dict                           │    │
│  │                                                      │    │
│  │  init_asset_metadata()                             │    │
│  │   → get symbols                                    │    │
│  │   → create metadata for each                       │    │
│  │   → insert/update database                         │    │
│  └────────────────────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│             database/models.py                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │  AssetMetadata                                      │    │
│  │  - symbol (PK)                                      │    │
│  │  - sector (string, nullable)                        │    │
│  │  - is_meme (string: "true"/"false")                │    │
│  │  - liquidity_score (float)                          │    │
│  │  - market_cap_usd, instrument_type, ...           │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 Classification Examples

### Well-Known Cryptocurrencies (High Confidence)

| Symbol | Sector | is_meme | Confidence | Liquidity |
|--------|--------|---------|------------|-----------|
| BTC | Layer1 | false | high | 1.0 |
| ETH | Layer1 | false | high | 1.0 |
| DOGE | Meme | **true** | high | 0.8 |
| SHIB | Meme | **true** | high | 0.6 |
| PEPE | Meme | **true** | high | 0.6 |
| UNI | DeFi | false | high | 0.7 |
| ARB | Layer2 | false | high | 0.75 |
| LINK | Oracle | false | high | 0.7 |

### Pattern-Based Detection (Medium Confidence)

| Symbol | Pattern | Sector | is_meme | Confidence |
|--------|---------|--------|---------|------------|
| BABYINU | `*INU` | Meme | **true** | medium |
| ELONDOGE | `*DOGE*` | Meme | **true** | medium |
| SHIBARMY | `*SHIB*` | Meme | **true** | medium |
| BABYPEPE | `*BABY*` | Meme | **true** | medium |

### Unknown Tokens (Low Confidence)

| Symbol | Sector | is_meme | Confidence | Liquidity |
|--------|--------|---------|------------|-----------|
| NEWTOKEN | Unknown | false | low | 0.5 |
| RANDOM123 | Unknown | false | low | 0.5 |

---

## 🛠️ Usage in Code

### Python API

```python
from crypto_classification import classify_crypto

# Classify a single token
result = classify_crypto("DOGE")
print(result)
# {
#   'sector': 'Meme',
#   'is_meme': True,
#   'full_name': 'Dogecoin',
#   'confidence': 'high'
# }

# Add to database
from init_asset_metadata import add_custom_symbol

add_custom_symbol("PEPE")  # Auto-detect
add_custom_symbol("CUSTOM", sector="DeFi", is_meme=False)  # Manual override
```

### Integration with Trading System

The system automatically reads from `AI_TRADING_SYMBOLS` in `trading_commands.py`:

```python
# trading_commands.py
AI_TRADING_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]

# init_asset_metadata.py will automatically initialize these 6 symbols
# + additional mainstream tokens (AVAX, MATIC, ARB, OP, LINK, UNI, AAVE)
```

### Rule Evaluation Integration

```python
# Rule R1-01: Blacklist check
from database.connection import SessionLocal
from database.models import AssetMetadata

db = SessionLocal()
asset = db.query(AssetMetadata).filter(AssetMetadata.symbol == "DOGE").first()

if asset and asset.is_meme == "true":
    print("⚠️ Violation: Trading blacklisted meme coin")
    # Reject the trade

# Rule R2-03: Sector allocation
positions = get_all_positions(account_id)
for pos in positions:
    asset = db.query(AssetMetadata).filter(AssetMetadata.symbol == pos.symbol).first()
    sector = asset.sector if asset else "Unknown"
    # Calculate sector allocation...
```

---

## 📝 Command-Line Reference

### Initialize All Symbols

```bash
python init_asset_metadata.py
```

**Options:**
- `--no-update` - Skip updating existing records (only create new)

### Add Single Symbol

```bash
python init_asset_metadata.py --add <SYMBOL>
```

**Options:**
- `--sector <SECTOR>` - Override sector classification
  - Choices: Layer1, Layer2, DeFi, Exchange, Meme, GameFi, AI, Privacy, Oracle, Storage, Unknown
- `--is-meme <true/false>` - Override meme coin status
  - Values: `true`, `false`, or omit for auto-detection

**Examples:**

```bash
# Auto-detect everything
python init_asset_metadata.py --add PEPE
# → sector=Meme, is_meme=true (auto-detected)

# Override sector only
python init_asset_metadata.py --add CUSTOM --sector Layer1
# → sector=Layer1, is_meme=<auto-detected>

# Override both
python init_asset_metadata.py --add NEWTOKEN --sector DeFi --is-meme false
# → sector=DeFi, is_meme=false

# Mark as meme manually
python init_asset_metadata.py --add MEMECOIN --is-meme true
# → sector=<auto-detected>, is_meme=true
```

---

## 🔄 Data Flow

### Scenario 1: Application Startup

```
Application Startup
  │
  └─► startup.py
      └─► init_asset_metadata()
          │
          ├─► get_symbols_to_initialize()
          │   └─► AI_TRADING_SYMBOLS + mainstream tokens
          │
          └─► For each symbol:
              ├─► classify_crypto(symbol)
              │   └─► Check KNOWN_CLASSIFICATIONS
              │   └─► Check MEME_PATTERNS
              │   └─► Return classification
              │
              ├─► create_asset_metadata_from_symbol()
              │   └─► Apply liquidity mapping
              │   └─► Build metadata dict
              │
              └─► Insert/Update AssetMetadata table
```

### Scenario 2: Manual Addition

```
User runs CLI command
  │
  └─► python init_asset_metadata.py --add PEPE
      │
      └─► add_custom_symbol("PEPE", sector=None, is_meme=None)
          │
          ├─► create_asset_metadata_from_symbol("PEPE")
          │   └─► classify_crypto("PEPE")
          │       └─► KNOWN_CLASSIFICATIONS["PEPE"]
          │           → {sector: "Meme", is_meme: True}
          │
          ├─► No manual overrides (sector=None, is_meme=None)
          │
          └─► AssetMetadata.insert({
                  symbol: "PEPE",
                  sector: "Meme",
                  is_meme: "true",  # ← Automatically detected
                  liquidity_score: 0.6
              })
```

### Scenario 3: Rule Evaluation

```
AI makes trading decision
  │
  └─► RuleEvaluator.evaluate_r0_r1_gate()
      │
      └─► R1-01: Blacklist check
          │
          └─► asset = AssetMetadata.query(symbol="DOGE")
              │
              ├─► asset.is_meme == "true"?
              │   └─► YES → Violation detected
              │       └─► Return {rule: "R1-01", description: "Trading blacklisted asset"}
              │
              └─► NO → Continue to next rule
```

---

## 🎯 Supported Sectors

| Sector | Description | Examples |
|--------|-------------|----------|
| **Layer1** | Base-layer blockchains | BTC, ETH, SOL, ADA, AVAX, NEAR |
| **Layer2** | Scaling solutions | ARB, OP, MATIC, IMX, METIS |
| **DeFi** | Decentralized finance | AAVE, UNI, SUSHI, CRV, GMX |
| **Exchange** | Exchange tokens | BNB, OKB, CRO, FTT |
| **Meme** | Meme/community coins | DOGE, SHIB, PEPE, FLOKI, BONK |
| **GameFi** | Gaming/metaverse | AXS, SAND, MANA, GALA |
| **AI** | AI/ML tokens | FET, AGIX, RNDR, WLD, TAO |
| **Privacy** | Privacy coins | XMR, ZEC, DASH, SECRET |
| **Oracle** | Oracle networks | LINK, BAND, TRB, API3 |
| **Storage** | Decentralized storage | FIL, AR, STORJ, HNT |
| **Unknown** | Unclassified tokens | *Default for unknown* |

---

## 📋 Maintenance

### Adding New Known Cryptocurrencies

Edit [`crypto_classification.py`](../backend/crypto_classification.py):

```python
KNOWN_CLASSIFICATIONS: Dict[str, Dict[str, any]] = {
    # Add your new token here
    "NEWTOKEN": {
        "sector": "DeFi",
        "is_meme": False,
        "full_name": "New Token Protocol"
    },
    # ... existing entries
}
```

### Adding New Meme Patterns

Edit [`crypto_classification.py`](../backend/crypto_classification.py):

```python
MEME_PATTERNS = [
    r".*INU$",      # Existing pattern
    r".*NEWMEME.*", # Add your new pattern
    # ... existing patterns
]
```

### Updating Liquidity Scores

Edit [`init_asset_metadata.py`](../backend/init_asset_metadata.py):

```python
liquidity_map = {
    "BTC": 1.0,
    "ETH": 1.0,
    "NEWTOKEN": 0.95,  # Add custom liquidity
    # ... existing entries
}
```

---

## ⚠️ Important Notes

1. **Database Field Type**: `is_meme` is stored as **string** (`"true"` or `"false"`), not boolean
   - Reason: SQLite compatibility and explicit typing
   - Always use: `asset.is_meme == "true"` for checks

2. **Classification Confidence**:
   - `high` = Explicitly mapped in KNOWN_CLASSIFICATIONS
   - `medium` = Detected by pattern matching
   - `low` = Unknown token (conservative defaults applied)

3. **Symbol Normalization**:
   - Symbols are auto-cleaned: `BTC/USDC:USDC` → `BTC`
   - Always uppercase: `btc` → `BTC`

4. **Update vs Create**:
   - `init_asset_metadata()` updates existing records by default
   - Use `--no-update` to skip existing records

5. **Integration with Trading System**:
   - Automatically reads from `AI_TRADING_SYMBOLS` in trading_commands.py
   - Adds 7 additional mainstream tokens automatically
   - Total initialized: ~13 symbols by default

---

## 🔗 Related Files

- [`crypto_classification.py`](../backend/crypto_classification.py) - Classification engine
- [`init_asset_metadata.py`](../backend/init_asset_metadata.py) - Database initialization
- [`database/models.py`](../backend/database/models.py) - AssetMetadata model definition

---

## 📞 Support

For questions or issues:
1. Check logs for detailed classification decisions
2. Use `--add <SYMBOL>` with `--sector` and `--is-meme` for manual overrides

---

**Last Updated**: 2026-01-18  
**System Version**: 1.0  
**Database Schema**: AssetMetadata v2 (with auto-classification support)
