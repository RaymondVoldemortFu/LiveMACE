"""Thin HTTP adapter for order queries and synchronous trade commands."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from schemas.order import OrderOut
from services.http_order_service import (
    HttpOrderService,
    OrderServiceError,
    get_http_order_service,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class OrderCreateRequest(BaseModel):
    user_id: int
    symbol: str
    name: str
    market: str = "CRYPTO"
    side: str
    order_type: str
    price: Optional[float] = None
    quantity: float
    leverage: int = 1
    username: Optional[str] = None
    password: Optional[str] = None
    session_token: Optional[str] = None


class OrderExecutionResult(BaseModel):
    order_id: int
    executed: bool
    message: str


class OrderProcessingResult(BaseModel):
    executed_count: int
    total_checked: int
    message: str


class OrderCancelResult(BaseModel):
    message: str
    order_id: int


class OrderHealthResult(BaseModel):
    status: str
    timestamp: int
    statistics: dict[str, int]
    message: str


def _call(operation):
    try:
        return operation()
    except OrderServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/create", response_model=OrderOut)
def create_new_order(
    request: OrderCreateRequest,
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(lambda: service.create(request.model_dump()))


@router.get("/pending", response_model=List[OrderOut])
def get_user_pending_orders(
    user_id: Optional[int] = None,
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(lambda: service.pending(user_id))


@router.get("/user/{user_id}", response_model=List[OrderOut])
def get_user_orders(
    user_id: int,
    status: Optional[str] = None,
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(lambda: service.user_orders(user_id, status))


@router.post("/execute/{order_id}", response_model=OrderExecutionResult)
def execute_order_manually(
    order_id: int,
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(lambda: service.execute(order_id))


@router.post("/cancel/{order_id}", response_model=OrderCancelResult)
def cancel_user_order(
    order_id: int,
    reason: str = "User cancelled",
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(lambda: service.cancel(order_id, reason))


@router.post("/process-all", response_model=OrderProcessingResult)
def process_all_orders(
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(service.process_all)


@router.get("/order/{order_id}", response_model=OrderOut)
def get_order_details(
    order_id: int,
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(lambda: service.order_details(order_id))


@router.get("/health", response_model=OrderHealthResult)
def orders_health_check(
    service: HttpOrderService = Depends(get_http_order_service),
):
    return _call(service.health)
