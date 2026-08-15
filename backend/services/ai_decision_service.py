"""
AI Decision Service - Handles AI model API calls for trading decisions
"""
import logging
import random
import json
import time
import os
from decimal import Decimal
from typing import Dict, Optional, List, Any

import requests
from sqlalchemy.orm import Session

from database.models import Position, Account, AIDecisionLog, AgentTrace
import uuid
import asyncio
from services.asset_calculator import calc_positions_market_value
from services.news_feed import fetch_latest_news

from services.agent.core import *
from services.agent.env_wrapper import *
from services.agent.llm_client import *
from services.agent.tools import *
from services.agent.public_apis_registry import register_public_api_tools
from services.agent.history_tool import HistoryTool
from services.container_service import ContainerService
from services.security.api_key_security import is_default_api_key, resolve_runtime_api_key
from services.tool_cache import tool_cache


logger = logging.getLogger(__name__)

SUPPORTED_SYMBOLS: Dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "DOGE": "Dogecoin",
    "XRP": "Ripple",
    "BNB": "Binance Coin",
}

def _is_default_api_key(api_key: str) -> bool:
    """Check if the API key is a default/placeholder key that should be skipped"""
    return is_default_api_key(api_key)


def _get_portfolio_data(db: Session, account: Account) -> Dict:
    """Get current portfolio positions and values"""
    positions = db.query(Position).filter(
        Position.account_id == account.id
    ).all()

    portfolio = {}
    for pos in positions:
        if float(pos.quantity) > 0:
            portfolio[pos.symbol] = {
                "quantity": float(pos.quantity),
                "avg_cost": float(pos.avg_cost),
                "current_value": float(pos.quantity) * float(pos.avg_cost),
                "side": (pos.side or "LONG").upper(),  # Include position direction
                "leverage": pos.leverage,  # Include leverage
                "market": pos.market,
            }

    return {
        "account_id": account.id,  # Added for rule validation
        "cash": float(account.current_cash),
        "frozen_cash": float(account.frozen_cash),
        "positions": portfolio,
        "total_assets": float(account.current_cash) + calc_positions_market_value(db, account.id)
    }



def _clip_reason_for_db(reason: object, max_bytes: int = 65000) -> str:
    """Keep reason within MySQL TEXT safe size (bytes)."""
    if reason is None:
        return "No reason provided"
    s = str(reason)
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    b = b[: max(0, max_bytes - 3)]
    while b:
        try:
            return b.decode("utf-8") + "..."
        except UnicodeDecodeError:
            b = b[:-1]
    return "..."


def save_ai_decision(db: Session, account_id: int, decision: Dict, portfolio: Dict, executed: bool = False, order_id: Optional[int] = None, execution_price: Optional[float] = None, execution_quantity: Optional[float] = None) -> None:
    """Save AI decision to the decision log"""
    try:
        # Check if logging should be skipped (e.g., when execute_trade already logged)
        if decision.get("skip_logging"):
            logger.info(f"Skipping AIDecisionLog for account {account_id} (execute_trade already logged)")
            return

        # Fetch account from database using account_id
        from database.models import Account as AccountModel
        fresh_account = db.query(AccountModel).filter(AccountModel.id == account_id).first()
        if not fresh_account:
            logger.error(f"Account with id {account_id} not found in database")
            return

        operation = decision.get("operation", "").lower() if decision.get("operation") else ""
        symbol_raw = decision.get("symbol")
        symbol = symbol_raw.upper() if symbol_raw else None
        target_portion = float(decision.get("target_portion_of_balance", 0)) if decision.get("target_portion_of_balance") is not None else 0.0
        reason = _clip_reason_for_db(decision.get("reason", "No reason provided"))
        trace_id = decision.get("trace_id")

        # Calculate previous portion for the symbol
        prev_portion = 0.0
        if operation in ["close", "hold"] and symbol:
            positions = portfolio.get("positions", {})
            if symbol in positions:
                symbol_value = positions[symbol]["current_value"]
                total_balance = portfolio["total_assets"]
                if total_balance > 0:
                    prev_portion = symbol_value / total_balance

        # Normalize leverage from decision
        try:
            leverage_val = int(decision.get("leverage", 1))
        except Exception:
            leverage_val = 1
        if leverage_val < 1:
            leverage_val = 1

        # Create decision log entry
        decision_log = AIDecisionLog(
            account_id=account_id,
            reason=reason,
            operation=operation,
            symbol=symbol if operation != "hold" else None,
            direction=decision.get("direction", "long"),
            prev_portion=Decimal(str(prev_portion)),
            target_portion=Decimal(str(target_portion)),
            total_balance=Decimal(str(portfolio["total_assets"])),
            executed="true" if executed else "false",
            order_id=order_id,
            execution_price=Decimal(str(execution_price)) if execution_price is not None else None,
            execution_quantity=Decimal(str(execution_quantity)) if execution_quantity is not None else None,
            leverage=leverage_val,
            trace_id=trace_id
        )

        db.add(decision_log)
        db.commit()

        symbol_str = symbol if symbol else "N/A"
        logger.info(f"Saved AI decision log for account_id={account_id}: {operation} {symbol_str} "
                   f"prev_portion={prev_portion:.4f} target_portion={target_portion:.4f} leverage={leverage_val} executed={executed}")

        # Create account snapshot after saving decision
        try:
            from services.snapshot_service import create_account_snapshot
            from datetime import datetime, timezone
            snapshot = create_account_snapshot(db, account_id, timestamp=datetime.now(timezone.utc).replace(tzinfo=None))
            if snapshot:
                logger.info(f"Created account snapshot for account_id={account_id}")
            else:
                logger.warning(f"Failed to create account snapshot for account_id={account_id}")
        except Exception as snapshot_err:
            logger.error(f"Error creating account snapshot: {snapshot_err}")

        # Save rule evaluation results if this is a rule-aware agent
        # Use fresh_account which is attached to the current session
        enable_rule_aware = getattr(fresh_account, 'enable_rule_aware', 'false')
        is_rule_aware = enable_rule_aware == 'true' or enable_rule_aware == True

        if is_rule_aware and "compliance_audit" in decision:
            _save_rule_evaluation(db, account_id, decision, trace_id)

    except Exception as err:
        logger.error(f"Failed to save AI decision log: {err}")
        db.rollback()


def _save_rule_evaluation(db: Session, account_id: int, decision: Dict, trace_id: Optional[str]) -> None:
    """Save rule evaluation results for rule-aware agents"""
    try:
        from database.models import RuleEvaluationResult
        from datetime import datetime

        logger.info(f"Saving rule evaluation for account_id={account_id}, trace_id={trace_id}")

        compliance_audit = decision.get("compliance_audit", {})
        llm_audit = decision.get("llm_audit", {})

        logger.info(f"compliance_audit keys: {list(compliance_audit.keys()) if compliance_audit else 'None'}")
        logger.info(f"llm_audit keys: {list(llm_audit.keys()) if llm_audit else 'None'}")

        # Extract compliance data
        # gate_pass 表示是否通过硬约束检查（R0和R1）用于记录和监控
        # PASS = 完全通过，ADJUSTED = 有软约束违规(R2)但允许执行，FAIL = 有硬约束违规(R0/R1)
        # Note: 即使 gate_pass=false (FAIL状态)，决策仍会执行，但会被标记和记录
        final_status = compliance_audit.get("final_status", "")
        gate_pass = final_status in ["PASS", "ADJUSTED"]
        logger.info(f"Gate pass: {gate_pass}, final_status={final_status} (execution continues regardless)")

        # Extract violations and classify by rule level (从rule_id提取：R0-xx, R1-xx, R2-xx)
        violations = compliance_audit.get("violations", [])
        r0_violations = [v for v in violations if str(v.get("rule_id", "")).startswith("R0")]
        r1_violations = [v for v in violations if str(v.get("rule_id", "")).startswith("R1")]
        r2_violations = [v for v in violations if str(v.get("rule_id", "")).startswith("R2")]
        logger.info(f"Violations: total={len(violations)}, R0={len(r0_violations)}, R1={len(r1_violations)}, R2={len(r2_violations)}")

        # Extract R2 scores
        r2_results = compliance_audit.get("r2_results", {})
        r2_scores = r2_results.get("rule_scores", {})
        logger.info(f"R2 scores: {list(r2_scores.keys()) if r2_scores else 'None'}")

        # Extract scores from compliance audit
        # s_rule_sat is calculated in compliance_auditor based on R2 average score
        # (forced to 0 if R0/R1 violations exist)
        s_rule_sat = compliance_audit.get("s_rule_sat")
        s_audit = llm_audit.get("final_normalized_score")

        logger.info(f"Scores: s_rule_sat={s_rule_sat}, s_audit={s_audit}")
        logger.info(f"Violations breakdown: R0={len(r0_violations)}, R1={len(r1_violations)}, R2={len(r2_violations)}")

        # Extract LLM audit details
        coverage_data = llm_audit.get("coverage", {})
        conflict_data = llm_audit.get("conflict", {})

        llm_audit_score = llm_audit.get("final_normalized_score")  # Final normalized score (0-1)
        llm_audit_coverage = coverage_data.get("score") if isinstance(coverage_data, dict) else None  # Coverage score (1-5)
        llm_audit_conflict = conflict_data.get("score") if isinstance(conflict_data, dict) else None  # Conflict score (1-5)

        logger.info(f"LLM audit details: score={llm_audit_score}, coverage={llm_audit_coverage}, conflict={llm_audit_conflict}")

        # Calculate final score
        # s_rule_sat is R2 average score (or 0 if R0/R1 violations)
        # final_score is the average of s_rule_sat and s_audit
        final_score = None
        if s_rule_sat is not None and s_audit is not None:
            # Equal weight: 50% rule satisfaction, 50% LLM audit
            final_score = (s_rule_sat + s_audit) / 2.0
        elif s_rule_sat is not None:
            final_score = s_rule_sat
        elif s_audit is not None:
            final_score = s_audit

        has_hard_violations = len(r0_violations) > 0 or len(r1_violations) > 0
        _fs = f"{final_score:.3f}" if final_score is not None else "None"
        _sr = f"{s_rule_sat:.3f}" if s_rule_sat is not None else "None"
        _sa = f"{s_audit:.3f}" if s_audit is not None else "None"
        logger.info(f"Final score: {_fs} (s_rule_sat={_sr}, s_audit={_sa}, has_hard_violations={has_hard_violations})")

        # Create evaluation record
        eval_result = RuleEvaluationResult(
            trace_id=trace_id,
            account_id=account_id,
            ts=datetime.utcnow(),
            gate_pass="true" if gate_pass else "false",
            r0_violations_json=json.dumps(r0_violations, ensure_ascii=False) if r0_violations else None,
            r1_violations_json=json.dumps(r1_violations, ensure_ascii=False) if r1_violations else None,
            r2_scores_json=json.dumps(r2_scores, ensure_ascii=False) if r2_scores else None,
            s_rule_sat=s_rule_sat,
            s_audit=s_audit,
            final_score=final_score,
            # LLM audit details (per-decision)
            llm_audit_score=llm_audit_score,
            llm_audit_coverage=llm_audit_coverage,
            llm_audit_conflict=llm_audit_conflict,
            llm_audit_json=json.dumps(llm_audit, ensure_ascii=False) if llm_audit else None
        )

        logger.info(f"Adding rule evaluation record to database...")
        db.add(eval_result)
        db.commit()
        logger.info(f"✓ Rule evaluation committed successfully")

        # Format scores for logging
        s_rule_sat_str = f"{s_rule_sat:.3f}" if s_rule_sat is not None else "N/A"
        s_audit_str = f"{s_audit:.3f}" if s_audit is not None else "N/A"
        final_score_str = f"{final_score:.3f}" if final_score is not None else "N/A"

        logger.info(f"Saved rule evaluation for account_id={account_id}: "
                   f"gate_pass={gate_pass}, s_rule_sat={s_rule_sat_str}, "
                   f"s_audit={s_audit_str}, final_score={final_score_str}")

    except Exception as err:
        logger.error(f"Failed to save rule evaluation results: {err}", exc_info=True)
        # Don't rollback here as we already committed decision_log


def get_active_ai_accounts(db: Session) -> List[Account]:
    """Get all active AI accounts that are not using default API key"""
    accounts = db.query(Account).filter(
        Account.is_active == "true",
        Account.account_type == "AI"
    ).all()

    if not accounts:
        return []

    # Filter out default accounts
    valid_accounts = [acc for acc in accounts if not _is_default_api_key(acc.api_key)]

    if not valid_accounts:
        logger.debug("No valid AI accounts found (all using default keys)")
        return []

    return valid_accounts


def call_agent_for_decision(
    account: Account,
    portfolio: Dict,
    prices: Dict[str, float],
    db: Session,
    decision_round_id: Optional[str] = None,
) -> Optional[Dict]:
    """基于 Agent（多轮+工具）的唯一决策接口。"""

    account_id = account.id
    account_name = getattr(account, "name", f"account_{account_id}")
    account_type = getattr(account, "agent_type", "react")
    account_model = account.model
    account_api_key = resolve_runtime_api_key(account.api_key)
    account_base_url = account.base_url

    if _is_default_api_key(account_api_key):
        logger.info(f"Skipping AI trading for account {account_name} - using default API key")
        return None

    # Lease a container for the agent session
    container_service = ContainerService()
    leased_container_id = container_service.lease_container(account_id)
    if not leased_container_id:
        logger.error(f"Failed to lease sandbox container for account {account_name} (ID: {account_id})")
        return {
            "operation": "hold",
            "symbol": "",
            "direction": "long",
            "target_portion_of_balance": 0.0,
            "leverage": 1,
            "reason": "Container unavailable, fallback hold",
        }

    trace_id = str(uuid.uuid4())
    step_counter = 0
    created_local_round = False

    def on_step(message: Dict[str, Any]):
        nonlocal step_counter
        step_counter += 1
        try:
            role = message.get("role", "unknown")
            content = message.get("content")
            if content in (None, ""):
                # Some OpenAI-compatible providers return reasoning text in
                # reasoning_content while keeping content=null when tool_calls exist.
                reasoning_content = message.get("reasoning_content")
                if reasoning_content not in (None, ""):
                    content = reasoning_content
                else:
                    reasoning = message.get("reasoning")
                    if reasoning not in (None, ""):
                        content = reasoning

            # Skip saving if content is empty and no tool_calls (empty assistant response)
            tool_calls_data = message.get("tool_calls")
            if role == "assistant" and not content and not tool_calls_data:
                logger.debug(f"Skipping empty assistant response at step {step_counter}")
                step_counter -= 1  # Don't count empty responses
                return

            # Handle tool calls serialization
            tool_calls_str = None
            if tool_calls_data:
                tool_calls_list = []
                for t in tool_calls_data:
                    if isinstance(t, dict):
                        tool_calls_list.append(t)
                    elif hasattr(t, "function") and hasattr(t, "id"):
                        tool_calls_list.append(LLMClient._tool_call_dict_roundtrip(t))
                    elif hasattr(t, "model_dump"):
                        tool_calls_list.append(t.model_dump())
                    elif hasattr(t, "dict"):
                        tool_calls_list.append(t.dict())
                    else:
                        tool_calls_list.append(str(t))
                tool_calls_str = json.dumps(tool_calls_list, ensure_ascii=False)

            # For tool output, content is the output
            tool_output_str = None
            if role == "tool":
                tool_output_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)

            trace = AgentTrace(
                trace_id=trace_id,
                account_id=account_id,
                step_number=step_counter,
                role=role,
                content=str(content) if content is not None else None,
                tool_calls=tool_calls_str,
                tool_output=tool_output_str
            )
            db.add(trace)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to save agent trace: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    try:
        llm = LLMClient(
            model=account_model,
            api_key=account_api_key,
            base_url=account_base_url,  # 注意要和 OpenAI SDK 预期的 base_url 对齐
        )

        # Check account capability switches before tool registration.
        enable_rule_aware = getattr(account, 'enable_rule_aware', 'false')
        is_rule_aware = enable_rule_aware == 'true' or enable_rule_aware == True
        tool_routing_raw = getattr(account, "tool_routing_enabled", "true")
        tool_routing_enabled = (
            tool_routing_raw is True
            or (
                isinstance(tool_routing_raw, str)
                and tool_routing_raw.strip().lower() in {"1", "true", "yes", "on"}
            )
        )

        registry = ToolRegistry()
        register_default_tools(registry, db, account_id, trace_id=trace_id, runtime_api_key=account_api_key)

        # Register public-apis tools only when tool routing is enabled.
        # Rule-aware agents also skip public-apis to avoid tool namespace pollution and provider limits.
        if tool_routing_enabled and not is_rule_aware:
            try:
                public_api_tool_limit = max(0, int(os.getenv("PUBLIC_API_TOOL_LIMIT", "80")))
                public_api_limit = public_api_tool_limit or None
                registered_count = register_public_api_tools(registry, limit=public_api_limit)
                logger.info(
                    "Registered %s public-apis tools (limit=%s)",
                    registered_count,
                    public_api_limit if public_api_limit is not None else "unlimited",
                )
            except Exception as e:
                logger.warning(f"Failed to register public-apis tools: {e}")
        else:
            logger.info(
                "Skipped public-apis registration for account %s: tool_routing_enabled=%s, is_rule_aware=%s",
                account_name,
                tool_routing_enabled,
                is_rule_aware,
            )

        # Register the new history tool
        registry.register(HistoryTool(db, account_id))

        logger.info(f"Initiating agent decision for account: {account_name} (ID: {account_id}) Type: {account_type}")

        logger.info(f"Account {account.name} - enable_rule_aware: {enable_rule_aware}, is_rule_aware: {is_rule_aware}")

        # Use factory to create agent based on account config
        if is_rule_aware:
            # Use rule-aware agent with rule evaluation pipeline
            logger.info(f"Creating Rule-Aware Agent for account {account.name}")
            agent = create_agent(
                agent_type="rule_aware",
                llm=llm,
                tools=registry,
                max_steps=AgentConfig.MAX_STEPS,
                user_id=str(account.id),
                account_id=account.id,
                agent_name=account_name,
                enable_llm_audit=True  # Enable LLM-based audit scoring
            )
            logger.info(f"Rule-Aware Agent created successfully for account {account.name}")
        else:
            # Use standard agent (react, multi_agent, advanced_multi_agent) without rule evaluation
            agent_type = getattr(account, "agent_type", "react")
            logger.info(f"Creating standard {agent_type} agent for account {account.name}")
            agent = create_agent(
                agent_type=agent_type,
                llm=llm,
                tools=registry,
                max_steps=AgentConfig.MAX_STEPS,
                user_id=str(account.id),
                agent_name=account_name
            )
            if hasattr(agent, "set_tool_routing_enabled"):
                agent.set_tool_routing_enabled(tool_routing_enabled)
                logger.info(f"Tool routing enabled={tool_routing_enabled} for account {account.name}")
            logger.info(f"Standard {agent_type} agent created successfully for account {account.name}")

        # Get account info before run (to avoid DetachedInstanceError later)
        account_id = account.id
        account_name = account.name

        if not decision_round_id:
            decision_round_id = tool_cache.create_round_id(scope=f"account_{account_id}")
            created_local_round = True

        logger.info(f"Calling agent.run() for account {account_name} with decision_round_id={decision_round_id}")
        with tool_cache.use_round(decision_round_id):
            decision = agent.run(
                portfolio=portfolio,
                prices=prices,
                on_step=on_step,
                trace_id=trace_id,
                decision_round_id=decision_round_id,
            )
        logger.info(f"Agent.run() completed for account {account_name}, decision: {decision}")

        if decision:
            decision["trace_id"] = trace_id

        logger.info(f"Agent decision for {account_name}: {decision}")
        return decision

    except Exception as e:
        logger.error(f"call_agent_for_decision failed: {e}", exc_info=True)
        return None
    finally:
        if decision_round_id and created_local_round:
            tool_cache.clear_round(decision_round_id)
        # Always release the container (use saved account_id to avoid DetachedInstanceError)
        container_service.release_container(account_id)
