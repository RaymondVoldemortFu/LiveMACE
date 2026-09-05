"""
Account and Asset Curve API Routes (Cleaned)
"""

import anyio
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
import logging
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    RateLimitError,
    BadRequestError,
)


from database.connection import get_db
from database.models import Account
from services.agent.llm_client import LLMClient
from config.api_feature_config import ApiFeatureConfig
from benchmark.builtin.prompts.preview import preview_system_prompt_for_account
from services.security.api_key_security import encrypt_api_key, mask_api_key_for_display
from services.account_api_service import AccountApiService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/list")
async def list_all_accounts(db: Session = Depends(get_db)):
    """Get all active accounts (for paper trading demo)"""
    try:
        account_service = AccountApiService(db)
        accounts = account_service.list_active_accounts()
        
        result = []
        for account in accounts:
            user = account_service.get_user(account.user_id)
            result.append({
                "id": account.id,
                "user_id": account.user_id,
                "username": user.username if user else "unknown",
                "name": account.name,
                "account_type": account.account_type,
                "agent_type": getattr(account, "agent_type", "react"),
                "memory_enabled": getattr(account, "memory_enabled", "false"),
                "tool_routing_enabled": getattr(account, "tool_routing_enabled", "true"),
                "enable_rule_aware": getattr(account, "enable_rule_aware", "false") == "true",
                "initial_capital": float(account.initial_capital),
                "current_cash": float(account.current_cash),
                "frozen_cash": float(account.frozen_cash),
                "model": account.model,
                "base_url": account.base_url,
                "api_key": mask_api_key_for_display(account.api_key),
                "is_active": account.is_active == "true"
            })
        
        return result
    except Exception as e:
        logger.error(f"Failed to list accounts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list accounts: {str(e)}")


@router.get("/decision-schedule")
async def get_decision_schedule():
    """Get AI decision scheduler status."""
    try:
        from services.scheduler import get_ai_trade_schedule_status

        status = get_ai_trade_schedule_status()
        next_utc = datetime.fromisoformat(status["next_decision_time_utc"])
        next_utc8 = next_utc.astimezone(timezone(timedelta(hours=8)))
        return {
            **status,
            "next_decision_time_utc8": next_utc8.isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get decision schedule: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get decision schedule: {str(e)}")


@router.get("/{account_id}/overview")
async def get_specific_account_overview(account_id: int, db: Session = Depends(get_db)):
    """Get overview for a specific account"""
    try:
        # Get the specific account
        account_service = AccountApiService(db)
        account = account_service.get_active_account(account_id)
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Calculate positions equity (NOT notional exposure)
        from services.asset_calculator import calc_positions_market_value
        positions_value = float(calc_positions_market_value(db, account.id) or 0.0)
        
        # Count positions and pending orders for this account
        positions_count, pending_orders = account_service.get_position_order_counts(account.id)
        
        # Get LLM audit statistics from rule_evaluation_results table
        llm_audit_stats = None
        try:
            audit_results = account_service.get_llm_audit_stats(account.id)

            if audit_results and audit_results.count > 0:
                llm_audit_stats = {
                    "count": audit_results.count,
                    "avg_score": round(float(audit_results.avg_score), 3) if audit_results.avg_score else None,
                    "avg_coverage": round(float(audit_results.avg_coverage), 2) if audit_results.avg_coverage else None,
                    "avg_conflict": round(float(audit_results.avg_conflict), 2) if audit_results.avg_conflict else None
                }
        except Exception as e:
            logger.warning(f"Failed to calculate LLM audit stats: {e}")

        return {
            "account": {
                "id": account.id,
                "name": account.name,
                "account_type": account.account_type,
                "agent_type": getattr(account, "agent_type", "react"),
                "current_cash": float(account.current_cash),
                "frozen_cash": float(account.frozen_cash),
            },
            "total_assets": positions_value + float(account.current_cash),
            "positions_value": positions_value,
            "positions_count": positions_count,
            "pending_orders": pending_orders,
            "llm_audit_stats": llm_audit_stats
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account {account_id} overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get account overview: {str(e)}")


@router.get("/{account_id}/system-prompt")
async def get_account_system_prompt(account_id: int, db: Session = Depends(get_db)):
    """Get rendered system prompt for a specific account."""
    try:
        account = AccountApiService(db).get_active_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        agent_type = (getattr(account, "agent_type", "react") or "react").strip().lower()
        memory_enabled = (getattr(account, "memory_enabled", "false") == "true")
        tool_routing_enabled = (getattr(account, "tool_routing_enabled", "true") == "true")
        preview = preview_system_prompt_for_account(account)

        return {
            "account_id": account.id,
            "account_name": account.name,
            "agent_type": agent_type,
            "agent_id": preview.agent_id,
            "memory_enabled": memory_enabled,
            "tool_routing_enabled": tool_routing_enabled,
            "decision_protocol": "tool",
            "termination_token": "<TRADE_DONE>",
            "system_prompt": preview.system_prompt,
            "prompt_profile_id": preview.prompt_profile_id,
            "prompt_profile_version": preview.prompt_profile_version,
            "prompt_id": preview.prompt_id,
            "prompt_version": preview.prompt_version,
            "prompt_hash": preview.prompt_hash,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account {account_id} system prompt: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get account system prompt: {str(e)}")



@router.get("/overview")
async def get_account_overview(db: Session = Depends(get_db)):
    """Get overview for the default account (for paper trading demo)"""
    try:
        # Get the first active account (default account)
        account_service = AccountApiService(db)
        account = account_service.get_default_active_account()
        
        if not account:
            raise HTTPException(status_code=404, detail="No active account found")
        
        # Calculate positions equity (NOT notional exposure)
        from services.asset_calculator import calc_positions_market_value
        positions_value = float(calc_positions_market_value(db, account.id) or 0.0)
        
        # Count positions and pending orders
        positions_count, pending_orders = account_service.get_position_order_counts(account.id)
        
        return {
            "account": {
                "id": account.id,
                "name": account.name,
                "account_type": account.account_type,
                "agent_type": getattr(account, "agent_type", "react"),
                "current_cash": float(account.current_cash),
                "frozen_cash": float(account.frozen_cash),
            },
            "portfolio": {
                "total_assets": positions_value + float(account.current_cash),
                "positions_value": positions_value,
                "positions_count": positions_count,
                "pending_orders": pending_orders,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get overview: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get overview: {str(e)}")


@router.post("/")
async def create_new_account(payload: dict, db: Session = Depends(get_db)):
    """Create a new account for the default user (for paper trading demo)"""
    try:
        if not ApiFeatureConfig.ENABLE_ACCOUNT_CREATION_API:
            raise HTTPException(
                status_code=403,
                detail="Account creation API is disabled by deployment configuration",
            )

        # Log incoming payload for debugging
        logger.info(f"Creating account with payload: {payload}")

        # Get the default user (or first user)
        account_service = AccountApiService(db)
        user = account_service.get_default_user()
        
        if not user:
            raise HTTPException(status_code=404, detail="No user found")
        
        # Validate required fields
        if "name" not in payload or not payload["name"]:
            raise HTTPException(status_code=400, detail="Account name is required")
        
        model = (payload.get("model") or "").strip() or None
        base_url = (payload.get("base_url") or "").strip() or None
        api_key = encrypt_api_key((payload.get("api_key") or "").strip() or None)

        # Create new account
        enable_rule_aware_value = "true" if payload.get("enable_rule_aware") is True else "false"
        logger.info(f"Setting enable_rule_aware to: {enable_rule_aware_value} (from {payload.get('enable_rule_aware')})")

        new_account = Account(
            user_id=user.id,
            version="v1",
            name=payload["name"],
            account_type=payload.get("account_type", "AI"),
            agent_type=payload.get("agent_type", "react"),
            memory_enabled=payload.get("memory_enabled", "false"),
            tool_routing_enabled=payload.get("tool_routing_enabled", "true"),
            enable_rule_aware=enable_rule_aware_value,
            model=model,
            base_url=base_url,
            api_key=api_key,
            initial_capital=float(payload.get("initial_capital", 10000.0)),
            current_cash=float(payload.get("initial_capital", 10000.0)),
            frozen_cash=0.0,
            is_active="true"
        )
        
        account_service.persist(new_account)
        
        logger.info(f"Account created successfully: ID={new_account.id}, name={new_account.name}, enable_rule_aware={new_account.enable_rule_aware}")

        # Reset auto trading job after creating new account. The reset runs
        # synchronous market warmup, so it must execute in a worker thread to
        # keep the event loop responsive.
        try:
            from services.scheduler import reset_auto_trading_job
            await anyio.to_thread.run_sync(reset_auto_trading_job)
            logger.info("Auto trading job reset successfully after account creation")
        except Exception as e:
            logger.warning(f"Failed to reset auto trading job: {e}")
        
        return {
            "id": new_account.id,
            "user_id": new_account.user_id,
            "username": user.username,
            "name": new_account.name,
            "account_type": new_account.account_type,
            "agent_type": new_account.agent_type,
            "memory_enabled": new_account.memory_enabled,
            "tool_routing_enabled": new_account.tool_routing_enabled,
            "enable_rule_aware": new_account.enable_rule_aware == "true",
            "initial_capital": float(new_account.initial_capital),
            "current_cash": float(new_account.current_cash),
            "frozen_cash": float(new_account.frozen_cash),
            "model": new_account.model,
            "base_url": new_account.base_url,
            "api_key": mask_api_key_for_display(new_account.api_key),
            "is_active": new_account.is_active == "true"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create account: {str(e)}")


@router.put("/{account_id}")
async def update_account_settings(account_id: int, payload: dict, db: Session = Depends(get_db)):
    """Update account settings (for paper trading demo)"""
    try:
        if not ApiFeatureConfig.ENABLE_ACCOUNT_UPDATE_API:
            raise HTTPException(
                status_code=403,
                detail="Account update API is disabled by deployment configuration",
            )

        logger.info(f"Updating account {account_id} with payload: {payload}")
        
        account_service = AccountApiService(db)
        account = account_service.get_active_account(account_id)
        
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        
        # Update fields if provided (allow empty strings for api_key and base_url)
        if "name" in payload:
            if payload["name"]:
                account.name = payload["name"]
                logger.info(f"Updated name to: {payload['name']}")
            else:
                raise HTTPException(status_code=400, detail="Account name cannot be empty")
        
        if "model" in payload:
            account.model = payload["model"] if payload["model"] else None
            logger.info(f"Updated model to: {account.model}")
        
        if "agent_type" in payload:
            account.agent_type = payload["agent_type"]
            logger.info(f"Updated agent_type to: {account.agent_type}")

        if "memory_enabled" in payload:
            account.memory_enabled = payload["memory_enabled"]
            logger.info(f"Updated memory_enabled to: {account.memory_enabled}")

        if "tool_routing_enabled" in payload:
            account.tool_routing_enabled = payload["tool_routing_enabled"]
            logger.info(f"Updated tool_routing_enabled to: {account.tool_routing_enabled}")

        if "enable_rule_aware" in payload:
            account.enable_rule_aware = "true" if payload["enable_rule_aware"] is True else "false"
            logger.info(f"Updated enable_rule_aware to: {account.enable_rule_aware}")

        if "base_url" in payload:
            account.base_url = payload["base_url"]
            logger.info(f"Updated base_url to: {account.base_url}")
        
        if "api_key" in payload:
            incoming_api_key = (payload["api_key"] or "").strip() if payload["api_key"] is not None else None
            account.api_key = encrypt_api_key(incoming_api_key)
            logger.info(
                f"Updated api_key (input_length: {len(incoming_api_key) if incoming_api_key else 0}, stored_as_encrypted: {bool(incoming_api_key)})"
            )
        
        account_service.persist(account)
        logger.info(f"Account {account_id} updated successfully")
        
        # Reset auto trading job after account update. Runs in a worker
        # thread because the reset performs synchronous market warmup.
        try:
            from services.scheduler import reset_auto_trading_job
            await anyio.to_thread.run_sync(reset_auto_trading_job)
            logger.info("Auto trading job reset successfully after account update")
        except Exception as e:
            logger.warning(f"Failed to reset auto trading job: {e}")
        
        user = account_service.get_user(account.user_id)
        
        return {
            "id": account.id,
            "user_id": account.user_id,
            "username": user.username if user else "unknown",
            "name": account.name,
            "account_type": account.account_type,
            "agent_type": getattr(account, "agent_type", "react"),
            "memory_enabled": getattr(account, "memory_enabled", "false"),
            "tool_routing_enabled": getattr(account, "tool_routing_enabled", "true"),
            "enable_rule_aware": getattr(account, "enable_rule_aware", "false") == "true",
            "initial_capital": float(account.initial_capital),
            "current_cash": float(account.current_cash),
            "frozen_cash": float(account.frozen_cash),
            "model": account.model,
            "base_url": account.base_url,
            "api_key": mask_api_key_for_display(account.api_key),
            "is_active": account.is_active == "true"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update account: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update account: {str(e)}")


@router.get("/asset-curve/timeframe")
async def get_asset_curve_by_timeframe(
    timeframe: str = "1d",
    db: Session = Depends(get_db)
):
    """Get persisted asset curve data for all accounts within a timeframe."""
    try:
        from services.asset_curve_cache_service import (
            get_asset_curve_cache,
            get_curve_point_limit,
            refresh_asset_curve_cache,
        )

        point_limit = get_curve_point_limit(timeframe)

        cached = get_asset_curve_cache(db, timeframe, limit_timestamps=point_limit)
        if cached:
            return cached

        refreshed = refresh_asset_curve_cache(db, timeframe)
        if refreshed <= 0:
            raise HTTPException(
                status_code=503,
                detail="Asset curve cache is empty after refresh",
            )

        cached = get_asset_curve_cache(db, timeframe, limit_timestamps=point_limit)
        if not cached:
            raise HTTPException(
                status_code=500,
                detail="Asset curve cache refresh completed but no rows were persisted",
            )
        return cached
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as e:
        logger.error(f"Failed to get asset curve for timeframe: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get asset curve for timeframe: {str(e)}")

def _map_status_code_to_message(status_code, model):
    return {
        401: "Authentication failed. Please check your API key.",
        403: f"Permission denied for model '{model}'.",
        404: f"Model '{model}' not found or endpoint not available.",
        429: "Rate limit exceeded. Please try again later."
    }.get(status_code, f"HTTP {status_code} error")

@router.post("/test-llm")
async def test_llm_connection(payload: dict):
    """Test LLM connection with provided credentials"""
    try:
        model = (payload.get("model") or "").strip()
        base_url = (payload.get("base_url") or "").strip()
        api_key = (payload.get("api_key") or "").strip()
        
        logger.info(f"Testing LLM connection: model={model}, base_url={base_url}")

        timeout_seconds_raw = payload.get("timeout_seconds", 15)
        
        # Keep logs safe: never print full api key
        logger.info(
            "Testing LLM connection with payload keys: model=%s base_url=%s api_key_length=%s timeout_seconds=%s",
            model,
            base_url,
            len(api_key) if api_key else 0,
            timeout_seconds_raw,
        )


        if not model:
            return {"success": False, "message": "Model is required"}
        if not api_key:
            return {"success": False, "message": "API key is required"}
        if not base_url:
            return {"success": False, "message": "Base URL is required"}

        # Use the same client path as real agent runtime.
        # LLMClient is expected to handle both OpenAI-compatible endpoints and Gemini.
        # This also normalizes base_url formats like:
        # - https://host/v1
        # - https://host/v1/
        # - https://host/v1/chat/completions
        normalized_base_url = LLMClient.normalize_base_url(base_url)
        timeout_seconds = float(timeout_seconds_raw) if timeout_seconds_raw is not None else 15.0
        timeout_seconds = max(3.0, min(timeout_seconds, 120.0))

        logger.info(
            "LLM test params: model=%s normalized_base_url=%s timeout=%ss",
            model,
            normalized_base_url,
            timeout_seconds,
        )

        try:
            client = LLMClient(model=model, api_key=api_key, base_url=base_url)
            content = client.test_connection(timeout_seconds=timeout_seconds)

            if content:
                logger.info(
                    "LLM test successful for model=%s base_url=%s",
                    model,
                    normalized_base_url,
                )
                return {
                    "success": True,
                    "message": f"Connection successful! Model {model} responded correctly.",
                    "response": content,
                    "normalized_base_url": normalized_base_url,
                }

            return {
                "success": False,
                "message": "LLM responded but returned empty content.",
                "normalized_base_url": normalized_base_url,
            }

        except APITimeoutError:
            return {
                "success": False,
                "message": (
                    f"Request timed out after {timeout_seconds:.0f}s. "
                    "Please verify endpoint responsiveness or increase timeout_seconds."
                ),
                "normalized_base_url": normalized_base_url,
            }
        except AuthenticationError:
            return {
                "success": False,
                "message": _map_status_code_to_message(401, model),
                "normalized_base_url": normalized_base_url,
            }
        except PermissionDeniedError:
            return {
                "success": False,
                "message": _map_status_code_to_message(403, model),
                "normalized_base_url": normalized_base_url,
            }
        except NotFoundError:
            return {
                "success": False,
                "message": _map_status_code_to_message(404, model),
                "normalized_base_url": normalized_base_url,
            }
        except RateLimitError:
            return {
                "success": False,
                "message": _map_status_code_to_message(429, model),
                "normalized_base_url": normalized_base_url,
            }
        except BadRequestError as e:
            return {
                "success": False,
                "message": f"Invalid request: {str(e)}",
                "normalized_base_url": normalized_base_url,
            }
        except APIConnectionError as e:
            return {
                "success": False,
                "message": f"Failed to connect to {normalized_base_url}. Error: {str(e)}",
                "normalized_base_url": normalized_base_url,
            }
        except Exception as e:
            logger.error(f"LLM test failed: {str(e)}", exc_info=True)
            return {
                "success": False,
                "message": f"Connection test failed: {str(e)}",
                "normalized_base_url": normalized_base_url,
            }
          
    except Exception as e:
        logger.error(f"Failed to test LLM connection: {e}", exc_info=True)
        return {"success": False, "message": f"Failed to test LLM connection: {str(e)}"}
