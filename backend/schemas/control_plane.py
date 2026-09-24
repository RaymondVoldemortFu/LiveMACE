"""Account, trace and portfolio wire contracts shared by HTTP and WebSocket clients."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TradingAccount(BaseModel):
    id: int
    user_id: int
    username: str
    name: str
    account_type: str
    agent_type: str | None
    memory_enabled: str | None
    tool_routing_enabled: str | None
    enable_rule_aware: bool
    initial_capital: float
    current_cash: float
    frozen_cash: float
    model: str | None
    base_url: str | None
    api_key: str | None
    is_active: bool


class TradingAccountCreate(BaseModel):
    name: str
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    agent_type: str = 'react'
    memory_enabled: str = 'false'
    tool_routing_enabled: str = 'true'
    enable_rule_aware: bool = False
    initial_capital: float = 10000
    account_type: str = 'AI'


class TradingAccountUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    agent_type: str | None = None
    memory_enabled: str | None = None
    tool_routing_enabled: str | None = None
    enable_rule_aware: bool | None = None


class AccountSystemPromptResponse(BaseModel):
    account_id: int
    account_name: str
    agent_type: str
    agent_id: str
    memory_enabled: bool
    tool_routing_enabled: bool
    decision_protocol: str
    termination_token: str
    system_prompt: str
    prompt_profile_id: str | None
    prompt_profile_version: str | None
    prompt_id: str | None
    prompt_version: str | None
    prompt_hash: str | None


class DecisionSchedule(BaseModel):
    job_id: str
    interval_seconds: str
    first_execution_time: str
    next_decision_time_utc: str
    next_decision_time_utc8: str


class AgentStep(BaseModel):
    step_number: int
    role: str
    content: str | None
    tool_calls: Any
    tool_output: Any
    created_at: datetime | None


class RuntimeEvent(BaseModel):
    id: str
    type: str
    account_id: int
    decision_round_id: str
    payload: dict[str, Any]
    created_at: datetime


class AgentTrace(BaseModel):
    trace_id: str
    steps: list[AgentStep]
    events: list[RuntimeEvent]
    schema_version: int


class TraceSummary(BaseModel):
    trace_id: str
    timestamp: datetime
    operation: str
    symbol: str | None
    reason: str


class PortfolioUser(BaseModel):
    id: int
    username: str


class PortfolioAccount(BaseModel):
    id: int
    user_id: int
    name: str
    account_type: str
    initial_capital: float
    current_cash: float
    frozen_cash: float
    enable_rule_aware: bool | None = None


class PortfolioOverview(BaseModel):
    account: PortfolioAccount
    return_rate: float
    total_notional_value: float
    positions_notional_value: float
    total_assets: float
    positions_market_value: float


class PortfolioPosition(BaseModel):
    id: int
    account_id: int | None = None
    user_id: int | None = None
    symbol: str
    name: str
    market: str
    quantity: float
    available_quantity: float
    side: str | None = None
    avg_cost: float
    leverage: float
    last_price: float | None
    market_value: float | None
    notional_value: float | None


class PortfolioOrder(BaseModel):
    id: int
    order_no: str
    user_id: int
    symbol: str
    name: str
    market: str
    side: str
    order_type: str
    price: float | None
    quantity: float
    leverage: float
    filled_quantity: float
    status: str


class PortfolioTrade(BaseModel):
    id: int
    order_id: int
    user_id: int
    symbol: str
    name: str
    market: str
    side: str
    price: float
    quantity: float
    commission: float
    trade_time: str


class AIDecision(BaseModel):
    id: int
    decision_time: str
    operation: str
    symbol: str | None
    prev_portion: float
    target_portion: float
    total_balance: float
    leverage: float | None
    executed: str
    reason: str | None
    order_id: int | None


class AssetCurvePoint(BaseModel):
    timestamp: int
    datetime_str: str
    total_assets: float
    initial_capital: float
    profit: float
    user_id: int
    username: str


class PersistedAssetCurvePoint(AssetCurvePoint):
    account_id: int
    profit_percentage: float
    cash: float
    positions_value: float


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(extra='allow')
    type: str
    overview: PortfolioOverview
    positions: list[PortfolioPosition]
    orders: list[PortfolioOrder]
    trades: list[PortfolioTrade]
    ai_decisions: list[AIDecision]
    all_asset_curves: list[AssetCurvePoint] | None = None
