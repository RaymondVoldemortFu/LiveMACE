"""
AI Decision Service - Handles AI model API calls for trading decisions
"""
import logging
import random
import json
import time
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
from services.agent.history_tool import HistoryTool
from services.container_service import ContainerService


logger = logging.getLogger(__name__)

#  mode API keys that should be skipped
DEMO_API_KEYS = {
    "default-key-please-update-in-settings",
    "default",
    "",
    None
}

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
    return api_key in DEMO_API_KEYS


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


def call_ai_for_decision(account: Account, portfolio: Dict, prices: Dict[str, float]) -> Optional[Dict]:
    """Call AI model API to get trading decision"""
    # Check if this is a default API key
    if _is_default_api_key(account.api_key):
        logger.info(f"Skipping AI trading for account {account.name} - using default API key")
        return None

    try:
        news_summary = fetch_latest_news()
        news_section = news_summary if news_summary else "No recent CoinJournal news available."

        prompt = f"""You are a cryptocurrency trading AI. Based on the following portfolio and market data, decide on a trading action.

Portfolio Data:
- Cash Available: ${portfolio['cash']:.2f}
- Frozen Cash: ${portfolio['frozen_cash']:.2f}
- Total Assets: ${portfolio['total_assets']:.2f}
- Current Positions (each shows quantity, avg_cost, current_value, side: LONG/SHORT, leverage): 
{json.dumps(portfolio['positions'], indent=2)}

Current Market Prices:
{json.dumps(prices, indent=2)}

Latest Crypto News (CoinJournal):
{news_section}

Analyze the market and portfolio, then respond with ONLY a JSON object in this exact format:
{{
  "operation": "open" or "close" or "hold",
  "symbol": "BTC" or "ETH" or "SOL" or "BNB" or "XRP" or "DOGE",
  "direction": "long" or "short",
  "target_portion_of_balance": 0.2,
  "leverage": 3,
  "reason": "Brief explanation of your decision"
}}

Rules:
- Only ONE position per coin allowed.
- operation must be "open", "close", or "hold"
- direction must be "long" or "short"
- For "open": Open a new position. You can open LONG (betting price goes up) or SHORT (betting price goes down)
  - symbol: which coin to trade
  - direction: "long" or "short"
  - target_portion_of_balance: % of available cash to use (0.0-1.0)
  - leverage: leverage multiplier (1-10, higher = more risk/reward)
- For "close": Close an existing position
  - symbol: which coin position to close
  - direction: must match the position side you want to close ("long" or "short")
  - target_portion_of_balance: % of position to close (0.0-1.0, use 1.0 to close entire position)
- For "hold": no action taken, direction can be omitted
- IMPORTANT: You can only hold ONE position per coin at a time (either long OR short, not both)
- Before opening a new position, check Current Positions to see if you already have a position on that coin
- You can only close positions that you currently hold (check Current Positions for side: "LONG" or "SHORT")
- leverage is typically 1-10x; only use high leverage (>5x) if you're very confident
- Only choose symbols you have price data for"""

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {account.api_key}"
        }

        # Use OpenAI-compatible chat completions format
        payload = {
            "model": account.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 1000
        }

        # Construct API endpoint URL
        # Remove trailing slash from base_url if present
        base_url = account.base_url.rstrip('/')
        # Use /chat/completions endpoint (OpenAI-compatible)
        api_endpoint = f"{base_url}/chat/completions"

        # Retry logic for rate limiting
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    api_endpoint,
                    headers=headers,
                    json=payload,
                    timeout=30,
                    verify=False  # Disable SSL verification for custom AI endpoints
                )

                if response.status_code == 200:
                    break  # Success, exit retry loop
                elif response.status_code == 429:
                    # Rate limited, wait and retry
                    wait_time = (2 ** attempt) + random.uniform(0, 1)  # Exponential backoff with jitter
                    logger.warning(f"AI API rate limited (attempt {attempt + 1}/{max_retries}), waiting {wait_time:.1f}s...")
                    if attempt < max_retries - 1:  # Don't wait on the last attempt
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"AI API rate limited after {max_retries} attempts: {response.text}")
                        return None
                else:
                    logger.error(f"AI API returned status {response.status_code}: {response.text}")
                    return None
            except requests.RequestException as req_err:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"AI API request failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time:.1f}s: {req_err}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"AI API request failed after {max_retries} attempts: {req_err}")
                    return None

        result = response.json()

        # Extract text from OpenAI-compatible response format
        if "choices" in result and len(result["choices"]) > 0:
            choice = result["choices"][0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            # Check if response was truncated due to length limit
            if finish_reason == "length":
                logger.warning(f"AI response was truncated due to token limit. Consider increasing max_tokens.")
                # Try to get content from reasoning field if available (some models put partial content there)
                text_content = message.get("reasoning", "") or message.get("content", "")
            else:
                text_content = message.get("content", "")

            if not text_content:
                logger.error(f"Empty content in AI response: {result}")
                return None

            # Try to extract JSON from the text
            # Sometimes AI might wrap JSON in markdown code blocks
            text_content = text_content.strip()
            if "```json" in text_content:
                text_content = text_content.split("```json")[1].split("```", 1)[0].strip()
            elif "```" in text_content:
                text_content = text_content.split("```", 1)[1].split("```", 1)[0].strip()

            # Handle potential JSON parsing issues with escape sequences
            try:
                decision = json.loads(text_content)
            except json.JSONDecodeError as parse_err:
                # Try to fix common JSON issues
                logger.warning(f"Initial JSON parse failed: {parse_err}")
                logger.warning(f"Problematic content: {text_content[:200]}...")

                # Try to clean up the text content
                cleaned_content = text_content

                # Replace problematic characters that might break JSON
                cleaned_content = cleaned_content.replace('\n', ' ')
                cleaned_content = cleaned_content.replace('\r', ' ')
                cleaned_content = cleaned_content.replace('\t', ' ')

                # Handle unescaped quotes in strings by escaping them
                import re
                # Try a simpler approach to fix common JSON issues
                # Replace smart quotes and em-dashes with regular equivalents
                cleaned_content = cleaned_content.replace('"', '"').replace('"', '"')
                cleaned_content = cleaned_content.replace('’', "'").replace('‘', "'")
                cleaned_content = cleaned_content.replace('–', '-').replace('—', '-')
                cleaned_content = cleaned_content.replace('‑', '-')  # Non-breaking hyphen

                # Try parsing again
                try:
                    decision = json.loads(cleaned_content)
                    logger.info("Successfully parsed JSON after cleanup")
                except json.JSONDecodeError:
                    # If still failing, try to extract just the essential parts
                    logger.error("JSON parsing failed even after cleanup, attempting manual extraction")
                    try:
                        # Extract operation, symbol, direction, portion, leverage, reason
                        operation_match = re.search(r'"operation":\s*"([^"]+)"', text_content)
                        symbol_match = re.search(r'"symbol":\s*"([^"]+)"', text_content)
                        direction_match = re.search(r'"direction":\s*"([^"]+)"', text_content)
                        portion_match = re.search(r'"target_portion_of_balance":\s*([0-9.]+)', text_content)
                        leverage_match = re.search(r'"leverage":\s*([0-9]+)', text_content)
                        reason_match = re.search(r'"reason":\s*"([^"]*)', text_content)

                        if operation_match and symbol_match and portion_match:
                            decision = {
                                "operation": operation_match.group(1),
                                "symbol": symbol_match.group(1),
                                "direction": direction_match.group(1).lower() if direction_match else "long",
                                "target_portion_of_balance": float(portion_match.group(1)),
                                "leverage": int(leverage_match.group(1)) if leverage_match else 1,
                                "reason": reason_match.group(1) if reason_match else "AI response parsing issue"
                            }
                            logger.info("Successfully extracted AI decision with direction and leverage manually")
                        else:
                            raise json.JSONDecodeError("Could not extract required fields", text_content, 0)
                    except Exception:
                        raise parse_err  # Re-raise original error

            # Validate that decision is a dict with required structure
            if not isinstance(decision, dict):
                logger.error(f"AI response is not a dict: {type(decision)}")
                return None

            logger.info(f"AI decision for {account.name}: {decision}")
            # 正常化leverage，未给时补1
            if "leverage" not in decision or not decision["leverage"]:
                decision["leverage"] = 1
            
            # 正常化direction，未给时补long
            if "direction" not in decision or not decision["direction"]:
                decision["direction"] = "long"
            else:
                decision["direction"] = decision["direction"].lower()
            return decision

        logger.error(f"Unexpected AI response format: {result}")
        return None

    except requests.RequestException as err:
        logger.error(f"AI API request failed: {err}")
        return None
    except json.JSONDecodeError as err:
        logger.error(f"Failed to parse AI response as JSON: {err}")
        # Try to log the content that failed to parse
        try:
            if 'text_content' in locals():
                logger.error(f"Content that failed to parse: {text_content[:500]}")
        except:
            pass
        return None
    except Exception as err:
        logger.error(f"Unexpected error calling AI: {err}", exc_info=True)
        return None


def save_ai_decision(db: Session, account_id: int, decision: Dict, portfolio: Dict, executed: bool = False, order_id: Optional[int] = None, execution_price: Optional[float] = None, execution_quantity: Optional[float] = None) -> None:
    """Save AI decision to the decision log"""
    try:
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
        reason = decision.get("reason", "No reason provided")
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
        logger.info(f"Final score: {final_score:.3f} (s_rule_sat={s_rule_sat:.3f}, s_audit={s_audit:.3f}, has_hard_violations={has_hard_violations})")

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
) -> Optional[Dict]:
    """基于 Agent（多轮+工具）的决策接口，保持与 call_ai_for_decision 兼容。"""

    account_id = account.id
    account_name = getattr(account, "name", f"account_{account_id}")
    account_type = getattr(account, "agent_type", "react")
    account_model = account.model
    account_api_key = account.api_key
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

        registry = ToolRegistry()
        register_default_tools(registry, db, account_id, trace_id=trace_id)
        
        # Register the new history tool
        registry.register(HistoryTool(db, account_id))

        logger.info(f"Initiating agent decision for account: {account_name} (ID: {account_id}) Type: {account_type}")
        
        # Check if rule-aware is enabled for this account
        enable_rule_aware = getattr(account, 'enable_rule_aware', 'false')
        is_rule_aware = enable_rule_aware == 'true' or enable_rule_aware == True

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
                enable_llm_audit=True  # Enable LLM-based audit scoring
            )
            logger.info(f"Rule-Aware Agent created successfully for account {account.name}")
        else:
            # Use standard agent (react or multi_agent) without rule evaluation
            agent_type = getattr(account, "agent_type", "react")
            logger.info(f"Creating standard {agent_type} agent for account {account.name}")
            agent = create_agent(
                agent_type=agent_type,
                llm=llm,
                tools=registry,
                max_steps=AgentConfig.MAX_STEPS,
                user_id=str(account.id)
            )
            logger.info(f"Standard {agent_type} agent created successfully for account {account.name}")

        # Get account info before run (to avoid DetachedInstanceError later)
        account_id = account.id
        account_name = account.name

        logger.info(f"Calling agent.run() for account {account_name}")
        decision = agent.run(portfolio=portfolio, prices=prices, on_step=on_step, trace_id=trace_id)
        logger.info(f"Agent.run() completed for account {account_name}, decision: {decision}")

        if decision:
            decision["trace_id"] = trace_id

        logger.info(f"Agent decision for {account_name}: {decision}")
        return decision

    except Exception as e:
        logger.error(f"call_agent_for_decision failed: {e}", exc_info=True)
        return None
    finally:
        # Always release the container (use saved account_id to avoid DetachedInstanceError)
        container_service.release_container(account_id)
