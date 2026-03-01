from sqlalchemy import Column, Integer, String, DECIMAL, TIMESTAMP, ForeignKey, UniqueConstraint, Float, Date, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import datetime

from .connection import Base


class User(Base):
    """
    User for authentication and account management
    In this project, use the default user, no user login
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=True)  # For future password authentication
    is_active = Column(String(10), nullable=False, default="true")
    
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    accounts = relationship("Account", back_populates="user")
    auth_sessions = relationship("UserAuthSession", back_populates="user")


class Account(Base):
    """Trading Account with AI model configuration"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    version = Column(String(100), nullable=False, default="v1")
    
    # Account Identity
    name = Column(String(100), nullable=False)  # Display name (e.g., "GPT Trader", "Claude Analyst")
    account_type = Column(String(20), nullable=False, default="AI")  # "AI" or "MANUAL"
    agent_type = Column(String(20), nullable=False, default="react") # "react" or "multi_agent"
    enable_rule_aware = Column(String(10), nullable=False, default="false")  # "true" or "false" - Enable Rule-Aware Trading
    is_active = Column(String(10), nullable=False, default="true")
    
    # AI Model Configuration (for AI accounts)
    model = Column(String(100), nullable=True)  # AI model name
    base_url = Column(String(500), nullable=True)  # API endpoint
    api_key = Column(String(500), nullable=True)  # API key for authentication
    
    # Trading Account Balances (USD for CRYPTO market)
    initial_capital = Column(DECIMAL(18, 2), nullable=False, default=10000.00)
    current_cash = Column(DECIMAL(18, 2), nullable=False, default=10000.00)
    frozen_cash = Column(DECIMAL(18, 2), nullable=False, default=0.00)
    
    # Margin for leverage trading
    margin_used = Column(DECIMAL(18, 2), nullable=False, default=0.00)
    maintenance_margin_ratio = Column(Float, nullable=False, default=0.10)  # 50% of initial margin
    
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User", back_populates="accounts")
    positions = relationship("Position", back_populates="account")
    orders = relationship("Order", back_populates="account")
    memories = relationship("AgentMemory", back_populates="account")


class UserAuthSession(Base):
    __tablename__ = "user_auth_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    
    user = relationship("User", back_populates="auth_sessions")


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(100), nullable=False, default="v1")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    symbol = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    market = Column(String(10), nullable=False)
    quantity = Column(DECIMAL(18, 8), nullable=False, default=0)  # Support fractional crypto amounts
    available_quantity = Column(DECIMAL(18, 8), nullable=False, default=0)
    avg_cost = Column(DECIMAL(18, 6), nullable=False, default=0)
    leverage = Column(Integer, nullable=False, default=1)  # 加权平均杠杆
    side = Column(String(10), nullable=True)  # 'LONG' or 'SHORT' for leveraged positions
    accumulated_interest = Column(DECIMAL(18, 6), nullable=False, default=0)  # 累计利息
    last_interest_time = Column(DateTime, nullable=True)  # 上次计息时间
    update_time = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account", back_populates="positions")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(100), nullable=False, default="v1")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    order_no = Column(String(32), unique=True, nullable=False)
    symbol = Column(String(20), nullable=False)  # e.g., 'BTC/USD'
    name = Column(String(100), nullable=False)   # e.g., 'Bitcoin'
    market = Column(String(10), nullable=False, default="CRYPTO")
    side = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)  # LIMIT, MARKET
    price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    leverage = Column(Integer, nullable=False, default=1)  # 1 for spot, >1 for leverage
    filled_quantity = Column(Float, nullable=False, default=0)
    status = Column(String(20), nullable=False)  # PENDING, FILLED, CANCELED
    order_time = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account", back_populates="orders")
    trades = relationship("Trade", back_populates="order")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    symbol = Column(String(20), nullable=False)  # e.g., 'BTC/USD'
    name = Column(String(100), nullable=False)   # e.g., 'Bitcoin'
    market = Column(String(10), nullable=False, default="CRYPTO")
    side = Column(String(10), nullable=False)
    price = Column(DECIMAL(18, 6), nullable=False)
    quantity = Column(DECIMAL(18, 8), nullable=False)  # Support fractional crypto amounts
    commission = Column(DECIMAL(18, 6), nullable=False, default=0)
    taker_fee = Column(DECIMAL(18, 6), nullable=False, default=0)  # 开仓/平仓手续费
    interest_charged = Column(DECIMAL(18, 6), nullable=False, default=0)  # 此笔交易产生的利息
    trade_time = Column(TIMESTAMP, server_default=func.current_timestamp())

    order = relationship("Order", back_populates="trades")


class TradingConfig(Base):
    __tablename__ = "trading_configs"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(100), nullable=False, default="v1")
    market = Column(String(10), nullable=False)
    min_commission = Column(Float, nullable=False)
    commission_rate = Column(Float, nullable=False)
    exchange_rate = Column(Float, nullable=False, default=1.0)
    min_order_quantity = Column(Integer, nullable=False, default=1)
    lot_size = Column(Integer, nullable=False, default=1)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    __table_args__ = (UniqueConstraint('market', 'version'),)


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(String(5000), nullable=True)  # 增加到5000字符以支持长cookie
    description = Column(String(500), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class CryptoPrice(Base):
    __tablename__ = "crypto_prices"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(10), nullable=False, default="CRYPTO")
    price = Column(DECIMAL(18, 6), nullable=False)
    price_date = Column(Date, nullable=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    __table_args__ = (UniqueConstraint('symbol', 'market', 'price_date'),)


class MarketKline(Base):
    __tablename__ = "market_klines"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(10), nullable=False, default="CRYPTO")
    period = Column(String(10), nullable=False)  # 1m, 5m, 15m, 30m, 1h, 1d
    timestamp = Column(Integer, nullable=False, index=True)
    datetime_str = Column(String(50), nullable=False)
    open_price = Column(DECIMAL(18, 6), nullable=True)
    high_price = Column(DECIMAL(18, 6), nullable=True)
    low_price = Column(DECIMAL(18, 6), nullable=True)
    close_price = Column(DECIMAL(18, 6), nullable=True)
    volume = Column(DECIMAL(18, 2), nullable=True)
    amount = Column(DECIMAL(18, 2), nullable=True)
    change = Column(DECIMAL(18, 6), nullable=True)
    percent = Column(DECIMAL(10, 4), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (UniqueConstraint('symbol', 'market', 'period', 'timestamp'),)


class AIDecisionLog(Base):
    __tablename__ = "ai_decision_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    decision_time = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    reason = Column(String(1000), nullable=False)  # AI reasoning for the decision
    operation = Column(String(10), nullable=False)  # open/close/hold
    symbol = Column(String(20), nullable=True)  # symbol for buy/sell operations
    prev_portion = Column(DECIMAL(10, 6), nullable=False, default=0)  # previous balance portion
    target_portion = Column(DECIMAL(10, 6), nullable=False)  # target balance portion
    total_balance = Column(DECIMAL(18, 2), nullable=False)  # total balance at decision time
    executed = Column(String(10), nullable=False, default="false")  # whether the decision was executed
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)  # linked order if executed
    leverage = Column(Integer, nullable=False, default=1)
    trace_id = Column(String(36), nullable=True)  # UUID for linking to detailed traces
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    # Relationships
    account = relationship("Account")
    order = relationship("Order")


class AgentTrace(Base):
    """Detailed execution trace of the agent"""
    __tablename__ = "agent_traces"

    id = Column(Integer, primary_key=True, index=True)
    trace_id = Column(String(36), nullable=False, index=True)  # Shared ID for a session
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    step_number = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)  # user, assistant, tool, system
    content = Column(String(50000), nullable=True)  # Large text content
    tool_calls = Column(String(50000), nullable=True)  # JSON string
    tool_output = Column(String(50000), nullable=True)  # JSON string
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    account = relationship("Account")


class AgentPeriodCheckpoint(Base):
    """Periodic performance checkpoint for an agent/account.

    Stores the account equity snapshot at the end of each fixed time slice so we can
    compare agents fairly over time (like checkpoints).
    """

    __tablename__ = "agent_period_checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)

    # The fixed slice size in seconds (e.g. 3600 for 1h)
    interval_seconds = Column(Integer, nullable=False, index=True)
    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False, index=True)

    equity_start = Column(DECIMAL(18, 6), nullable=False)
    equity_end = Column(DECIMAL(18, 6), nullable=False)
    pnl = Column(DECIMAL(18, 6), nullable=False)
    return_rate = Column(Float, nullable=False)  # pnl / equity_start
    volatility = Column(Float, nullable=False, default=0.0)  # rolling stddev of return_rate

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    account = relationship("Account")

    __table_args__ = (
        UniqueConstraint("account_id", "interval_seconds", "period_end"),
    )


class AgentMemory(Base):
    """Memory storage for agents"""
    __tablename__ = "agent_memories"

    id = Column(Integer, primary_key=True, index=True)
    memory_id = Column(String(36), unique=True, nullable=False, index=True)  # ID from Mem0 or UUID
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    trace_id = Column(String(36), nullable=True, index=True)  # Linked conversation/trace ID
    
    content = Column(Text, nullable=False)  # The actual memory text
    metadata_json = Column(JSON, nullable=True)  # Extra metadata (key-value)
    
    # Vector DB Info (optional, if we want to track it)
    vector_id = Column(String(100), nullable=True)
    
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    # Expiration logic
    expires_at = Column(DateTime, nullable=True)
    
    account = relationship("Account", back_populates="memories")


# CRYPTO market trading configuration constants
CRYPTO_MIN_COMMISSION = 0.1  # $0.1 minimum commission
CRYPTO_COMMISSION_RATE = 0.001  # 0.1% commission rate
CRYPTO_MIN_ORDER_QUANTITY = 0.0001  # Minimum 0.0001 BTC (supports fractional crypto)
CRYPTO_LOT_SIZE = 0.0001  # Lot size for crypto

# Leverage trading constants (Hyperliquid-style)
CRYPTO_TAKER_FEE_RATE = 0.0007  # 0.07% taker fee
CRYPTO_INTEREST_RATE_HOURLY = 0.0000125  # 0.00125%/hour (0.03%/day)
CRYPTO_MAX_LEVERAGE = 50  # Maximum leverage allowed
CRYPTO_MAINTENANCE_MARGIN_RATIO = 0.5  # 50% of initial margin


class AccountSnapshot(Base):
    """
    Account Snapshot - Historical account state for calculating metrics like 
    drawdown, volatility, average cash ratio, and turnover denominator
    """
    __tablename__ = "account_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    ts = Column(DateTime, nullable=False, index=True)  # Snapshot timestamp (UTC)
    
    # Account state at this timestamp
    total_equity = Column(DECIMAL(18, 2), nullable=False)  # Total account value (cash + positions)
    cash = Column(DECIMAL(18, 2), nullable=False)  # Available cash
    positions_value = Column(DECIMAL(18, 2), nullable=False)  # Market value of all positions
    
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    
    # Relationships
    account = relationship("Account")
    
    __table_args__ = (
        # Ensure unique snapshot per account per timestamp
        UniqueConstraint('account_id', 'ts', name='uix_account_snapshot_time'),
    )


class AssetMetadata(Base):
    """
    Asset Metadata - Static information about tradable assets for rule evaluation
    (sector/theme classification, meme flag, etc.)
    """
    __tablename__ = "asset_metadata"

    symbol = Column(String(20), primary_key=True)  # e.g., BTC, ETH, DOGE
    sector = Column(String(100), nullable=True)  # Sector/theme: DeFi, Layer1, AI, Meme, etc.
    is_meme = Column(String(10), nullable=False, default="false")  # "true" or "false"
    
    # Optional additional metadata (for future extensions)
    market_cap_usd = Column(DECIMAL(20, 2), nullable=True)  # Market cap in USD
    instrument_type = Column(String(20), nullable=True)  # spot, futures, perp, etc.
    liquidity_score = Column(Float, nullable=True)  # Custom liquidity rating
    
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class RuleEvaluationResult(Base):
    """
    Rule Evaluation Results - Stores structured compliance scoring results
    for each trading decision
    """
    __tablename__ = "rule_evaluation_results"

    id = Column(Integer, primary_key=True, index=True)
    trace_id = Column(String(36), nullable=True, index=True)  # Links to AgentTrace
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    ts = Column(DateTime, nullable=False, index=True)  # Evaluation timestamp
    
    # Gate(R0, R1) - Hard rule pass/fail
    gate_pass = Column(String(10), nullable=False, default="false")  # "true" or "false"
    
    # Violation details (JSON format)
    r0_violations_json = Column(Text, nullable=True)  # R0 (System Hard) violations
    r1_violations_json = Column(Text, nullable=True)  # R1 (Client Hard) violations
    r2_scores_json = Column(Text, nullable=True)  # R2 (Client Soft) individual scores
    
    # Compliance scores
    s_rule_sat = Column(Float, nullable=True)  # S_rule_sat: weighted soft rule score
    s_audit = Column(Float, nullable=True)  # S_audit: audit/awareness score (LLM-based)
    final_score = Column(Float, nullable=True)  # Final compliance score
    
    # LLM Audit Details (per-decision)
    llm_audit_score = Column(Float, nullable=True)  # Overall audit score (1.0-5.0)
    llm_audit_coverage = Column(Float, nullable=True)  # Coverage score (1.0-5.0)
    llm_audit_conflict = Column(Float, nullable=True)  # Conflict score (1.0-5.0)
    llm_audit_json = Column(Text, nullable=True)  # Full LLM audit response (JSON)
    
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    
    # Relationships
    account = relationship("Account")
