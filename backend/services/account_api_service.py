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

    def sync_runtime_switches(self, account, changed_fields):
        """Update derived Agent settings in the account transaction, preserving tool opt-outs."""
        from dataclasses import replace
        from database.models import AccountRuntimeConfig
        from benchmark.accounts.config import config_from_legacy_account
        from benchmark.accounts.service import _apply_config_to_row, _config_from_row
        from benchmark.accounts.validation import validate_extension_config
        from benchmark.extensions.host import get_extension_runtime

        row = self.db.query(AccountRuntimeConfig).filter(
            AccountRuntimeConfig.account_id == account.id).with_for_update().first()
        if row is None:
            return
        old = _config_from_row(row)
        derived = config_from_legacy_account(account)
        if {"agent_type", "enable_rule_aware"}.intersection(changed_fields) and derived.agent_id != old.agent_id:
            config = replace(derived, disabled_tools=old.disabled_tools, toolset_ids=old.toolset_ids)
        else:
            values = dict(old.agent_config)
            for key in ("memory_enabled", "tool_routing_enabled"):
                if key in changed_fields and key in derived.agent_config:
                    values[key] = derived.agent_config[key]
            config = replace(old, agent_config=values,
                prompt_profile_id=derived.prompt_profile_id if old.prompt_profile_id in {
                    "core.react.default", "core.react.memory", "core.react.tool-routing", "core.react.memory-tool-routing"
                } and old.agent_id == derived.agent_id else old.prompt_profile_id)
        runtime = get_extension_runtime()
        result = validate_extension_config(config, agent_registry=runtime.agents, prompt_registry=runtime.prompts)
        if not result.valid:
            raise ValueError('Updated account runtime configuration is invalid')
        _apply_config_to_row(row, result.resolved_config, result, existing=row)

    def persist(self, entity):
        try:
            self.db.add(entity)
            self.db.commit()
            self.db.refresh(entity)
        except Exception:
            self.db.rollback()
            raise
