"""
Rule Configuration API Routes - Basic rule statistics and summaries
"""
from fastapi import APIRouter, HTTPException
import logging

from benchmark.application.compliance.rules import RuleCatalog
from schemas.compliance import RuleSummary, RuleList

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.get("/summary", response_model=RuleSummary)
async def get_rule_summary():
    """
    Get basic rule statistics summary
    
    Returns:
        - Total rule count
        - Count by category (R0/R1/R2)
        - Brief category descriptions
    """
    try:
        return RuleCatalog().summary()
    except Exception as e:
        logger.error(f"Failed to load rule summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load rule summary: {str(e)}")


@router.get("/list", response_model=RuleList, response_model_exclude_unset=True)
async def list_all_rules():
    """
    Get list of all rules with basic information (ID, name, category)
    
    Returns simplified rule list without full descriptions for quick overview
    """
    try:
        return RuleCatalog().list_rules()
    except Exception as e:
        logger.error(f"Failed to load rules list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load rules list: {str(e)}")
