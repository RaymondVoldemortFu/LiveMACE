"""Synchronous application boundary for the legacy order HTTP contract."""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, Mapping

from benchmark.application.trading import (
    CancelOrderCommand,
    CreateOrderCommand,
    ProcessPendingOrders,
    get_default_trade_gateway,
)
from benchmark.contracts import Market
from config.api_feature_config import ApiFeatureConfig
from database.connection import SessionLocal
from database.models import Account, Order, User
from repositories.user_repo import (
    set_user_password,
    user_has_password,
    verify_auth_session,
    verify_user_password,
)
from services.order_matching import check_and_execute_order, get_pending_orders


class OrderServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass
class HttpOrderService:
    gateway: Any
    session_factory: Any = SessionLocal

    def create(self, request: Mapping[str, Any]):
        if not ApiFeatureConfig.ENABLE_MANUAL_ORDER_API:
            raise OrderServiceError(
                403, "Manual order API is disabled by deployment configuration"
            )
        db = self.session_factory()
        try:
            user = db.query(User).filter(User.id == request["user_id"]).first()
            if user is None:
                raise OrderServiceError(404, "User not found")
            self._authenticate(db, user, request)
            account = (
                db.query(Account)
                .filter(Account.user_id == user.id, Account.is_active == "true")
                .first()
            )
            if account is None:
                raise OrderServiceError(404, "Active trading account not found for user")
            account_id = account.id
        finally:
            db.close()

        result = self.gateway.create_order(
            CreateOrderCommand(
                account_id=account_id,
                symbol=str(request["symbol"]),
                market=Market(str(request.get("market") or "CRYPTO")),
                side=str(request["side"]),
                order_type=str(request["order_type"]),
                quantity=Decimal(str(request["quantity"])),
                price=(None if request.get("price") is None else Decimal(str(request["price"]))),
                leverage=int(request.get("leverage", 1)),
            )
        )
        if not result.accepted:
            raise OrderServiceError(
                400, result.reject_message or result.reject_code or "Order rejected"
            )
        return self.order_details(result.order_id)

    def _authenticate(self, db, user, request: Mapping[str, Any]) -> None:
        if request.get("session_token"):
            if verify_auth_session(db, request["session_token"]) != user.id:
                raise OrderServiceError(401, "Invalid or expired session")
            return
        if not request.get("username") or not request.get("password"):
            raise OrderServiceError(
                400, "Please provide either session token or username+password"
            )
        if user.username != request["username"]:
            raise OrderServiceError(401, "Username does not match")
        if not user_has_password(db, user.id):
            if len(str(request["password"]).strip()) < 4:
                raise OrderServiceError(400, "Password must be at least 4 characters")
            if not set_user_password(db, user.id, request["password"]):
                raise OrderServiceError(500, "Failed to set trading password")
        elif not verify_user_password(db, user.id, request["password"]):
            raise OrderServiceError(401, "Incorrect trading password")

    def pending(self, user_id: int | None):
        db = self.session_factory()
        try:
            return get_pending_orders(db, user_id)
        finally:
            db.close()

    def user_orders(self, user_id: int, status: str | None):
        db = self.session_factory()
        try:
            query = db.query(Order).filter(Order.user_id == user_id)
            if status:
                query = query.filter(Order.status == status)
            return query.order_by(Order.created_at.desc()).all()
        finally:
            db.close()

    def execute(self, order_id: int) -> dict[str, Any]:
        db = self.session_factory()
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order is None:
                raise OrderServiceError(404, "Order not found")
            if order.status != "PENDING":
                return {"order_id": order_id, "executed": False, "message": f"Order status is {order.status}, cannot execute"}
            executed = check_and_execute_order(db, order)
            return {
                "order_id": order_id,
                "executed": executed,
                "message": "Order executed successfully" if executed else "Order does not meet execution conditions",
            }
        finally:
            db.close()

    def cancel(self, order_id: int, reason: str) -> dict[str, Any]:
        db = self.session_factory()
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order is None:
                raise OrderServiceError(404, "Order not found")
            command = CancelOrderCommand(order.account_id, order.order_no, reason)
        finally:
            db.close()
        result = self.gateway.cancel_order(command)
        if not result.accepted:
            status = 404 if result.reject_code == "ORDER_NOT_FOUND" else 400
            raise OrderServiceError(status, result.reject_message or "Failed to cancel order")
        return {"message": "Order cancelled successfully", "order_id": order_id}

    def process_all(self) -> dict[str, Any]:
        result = self.gateway.process_pending(ProcessPendingOrders())
        return {
            "executed_count": result.executed,
            "total_checked": result.processed,
            "message": f"Processing complete: Checked {result.processed} orders, executed {result.executed}",
        }

    def order_details(self, order_id: int):
        db = self.session_factory()
        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if order is None:
                raise OrderServiceError(404, "Order not found")
            db.expunge(order)
            return order
        finally:
            db.close()

    def health(self) -> dict[str, Any]:
        db = self.session_factory()
        try:
            return {
                "status": "healthy",
                "timestamp": int(time.time() * 1000),
                "statistics": {
                    "total_orders": db.query(Order).count(),
                    "pending_orders": db.query(Order).filter(Order.status == "PENDING").count(),
                    "filled_orders": db.query(Order).filter(Order.status == "FILLED").count(),
                    "cancelled_orders": db.query(Order).filter(Order.status == "CANCELLED").count(),
                },
                "message": "Order service is running normally",
            }
        finally:
            db.close()


@lru_cache(maxsize=1)
def get_http_order_service() -> HttpOrderService:
    return HttpOrderService(get_default_trade_gateway())


__all__ = ["HttpOrderService", "OrderServiceError", "get_http_order_service"]
