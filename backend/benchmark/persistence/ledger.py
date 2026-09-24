"""SQLAlchemy ledger writer; applies plans in the caller-owned transaction."""

from benchmark.application.trading.planner import LedgerPlan
from database.models import Position, Trade, Order

ACCOUNT_FIELDS = ("id", "current_cash", "frozen_cash", "margin_used")
POSITION_FIELDS = (
    "version",
    "account_id",
    "symbol",
    "name",
    "market",
    "quantity",
    "available_quantity",
    "avg_cost",
    "leverage",
    "side",
    "last_interest_time",
    "accumulated_interest",
)
ORDER_FIELDS = (
    "id",
    "account_id",
    "symbol",
    "name",
    "market",
    "side",
    "quantity",
    "leverage",
    "order_no",
    "filled_quantity",
    "status",
    "price",
)


class SqlAlchemyLedgerRepository:
    """Read detached planner inputs and apply fields; never commit or calculate."""

    def __init__(self, db):
        self._db = db

    @staticmethod
    def account_values(account):
        return (
            {key: getattr(account, key) for key in ACCOUNT_FIELDS} if account else None
        )

    @staticmethod
    def order_values(order):
        return {key: getattr(order, key) for key in ORDER_FIELDS}

    def position_values(self, account_id, symbol, market):
        position = (
            self._db.query(Position)
            .filter_by(account_id=account_id, symbol=symbol, market=market)
            .first()
        )
        return (
            {key: getattr(position, key) for key in POSITION_FIELDS}
            if position
            else None
        )

    def create_order(self, plan):
        order = Order(**plan)
        self._db.add(order)
        self._db.flush()
        return order

    def inputs(self, account, order):
        position = (
            self._db.query(Position)
            .filter_by(account_id=account.id, symbol=order.symbol, market=order.market)
            .first()
        )

        def values(row, fields):
            return (
                {field: getattr(row, field) for field in fields}
                if row is not None
                else None
            )

        return (
            values(account, ACCOUNT_FIELDS),
            values(position, POSITION_FIELDS),
            values(order, ORDER_FIELDS),
        )

    def apply(self, plan: LedgerPlan, account, order):
        for key in ("current_cash", "frozen_cash", "margin_used"):
            if account is not None and key in plan.account:
                setattr(account, key, plan.account[key])
        if plan.position is not None:
            position = (
                self._db.query(Position)
                .filter_by(
                    account_id=account.id, symbol=order.symbol, market=order.market
                )
                .first()
            )
            if position is None:
                position = Position(**plan.position)
                self._db.add(position)
            else:
                for key, value in plan.position.items():
                    setattr(position, key, value)
        order.filled_quantity = plan.order["filled_quantity"]
        order.status = plan.order["status"]
        if plan.trade is not None:
            self._db.add(Trade(**plan.trade))
        self._db.flush()
