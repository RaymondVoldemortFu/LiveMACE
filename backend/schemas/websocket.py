"""Typed inbound WebSocket messages; wire field names remain unchanged."""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Message(BaseModel):
    model_config = ConfigDict(extra="allow")


class BootstrapMessage(_Message):
    type: Literal["bootstrap"]
    username: str = "default"
    initial_capital: float = 100000


class SubscribeMessage(_Message):
    type: Literal["subscribe"]
    user_id: int


class SwitchUserMessage(_Message):
    type: Literal["switch_user"]
    username: str


class SwitchAccountMessage(_Message):
    type: Literal["switch_account"]
    account_id: int


class GetSnapshotMessage(_Message):
    type: Literal["get_snapshot"]


class GetAssetCurveMessage(_Message):
    type: Literal["get_asset_curve"]
    timeframe: Literal["5m", "1h", "1d"] = "1h"


class PlaceOrderMessage(_Message):
    type: Literal["place_order"]
    symbol: str
    market: Literal["CRYPTO", "US"] = "CRYPTO"
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: float = Field(gt=0)
    price: float | None = None
    leverage: int = Field(default=1, ge=1, le=10)


class PingMessage(_Message):
    type: Literal["ping"]


InboundWebSocketMessage = Annotated[
    Union[
        BootstrapMessage,
        SubscribeMessage,
        SwitchUserMessage,
        SwitchAccountMessage,
        GetSnapshotMessage,
        GetAssetCurveMessage,
        PlaceOrderMessage,
        PingMessage,
    ],
    Field(discriminator="type"),
]

_ADAPTER = TypeAdapter(InboundWebSocketMessage)


def parse_websocket_message(value: object) -> InboundWebSocketMessage:
    return _ADAPTER.validate_python(value)


__all__ = ["InboundWebSocketMessage", "parse_websocket_message"]
