from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Account, Order, Position, RuleEvaluationResult, User


class AccountApiService:
    """Database boundary used by account HTTP adapters."""

    def __init__(self, db: Session):
        self.db = db

    def list_active_accounts(self):
        return self.db.query(Account).filter(Account.is_active == "true").all()

    def get_active_account(self, account_id: int):
        return self.db.query(Account).filter(
            Account.id == account_id,
            Account.is_active == "true",
        ).first()

    def get_default_active_account(self):
        return self.db.query(Account).filter(Account.is_active == "true").first()

    def get_user(self, user_id: int):
        return self.db.query(User).filter(User.id == user_id).first()

    def get_default_user(self):
        return self.db.query(User).filter(User.username == "default").first() or self.db.query(User).first()

    def get_position_order_counts(self, account_id: int):
        positions = self.db.query(Position).filter(
            Position.account_id == account_id,
            Position.quantity > 0,
        ).count()
        pending_orders = self.db.query(Order).filter(
            Order.account_id == account_id,
            Order.status == "PENDING",
        ).count()
        return positions, pending_orders

    def get_llm_audit_stats(self, account_id: int):
        return self.db.query(
            func.count(RuleEvaluationResult.id).label("count"),
            func.avg(RuleEvaluationResult.llm_audit_score).label("avg_score"),
            func.avg(RuleEvaluationResult.llm_audit_coverage).label("avg_coverage"),
            func.avg(RuleEvaluationResult.llm_audit_conflict).label("avg_conflict"),
        ).filter(
            RuleEvaluationResult.account_id == account_id,
            RuleEvaluationResult.llm_audit_score.isnot(None),
        ).first()

    def persist(self, entity):
        try:
            self.db.add(entity)
            self.db.commit()
            self.db.refresh(entity)
        except Exception:
            self.db.rollback()
            raise
