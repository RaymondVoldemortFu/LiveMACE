"""
Rule Configuration API Routes - Basic rule statistics and summaries
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
import os
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rules", tags=["rules"])


def load_rules_from_directory() -> Dict[str, Any]:
    """Load all rules from config directory"""
    rules_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "rules")
    
    result = {
        "r0_rules": [],
        "r1_rules": [],
        "r2_rules": []
    }
    
    # Load R0 rules
    r0_path = os.path.join(rules_dir, "r0_system_hard.json")
    if os.path.exists(r0_path):
        with open(r0_path, 'r', encoding='utf-8') as f:
            r0_data = json.load(f)
            result["r0_rules"] = r0_data.get("rules", [])
    
    # Load R1 rules
    r1_path = os.path.join(rules_dir, "r1_client_hard.json")
    if os.path.exists(r1_path):
        with open(r1_path, 'r', encoding='utf-8') as f:
            r1_data = json.load(f)
            result["r1_rules"] = r1_data.get("rules", [])
    
    # Load R2 rules
    r2_path = os.path.join(rules_dir, "r2_client_soft.json")
    if os.path.exists(r2_path):
        with open(r2_path, 'r', encoding='utf-8') as f:
            r2_data = json.load(f)
            result["r2_rules"] = r2_data.get("rules", [])
    
    return result


@router.get("/summary")
async def get_rule_summary():
    """
    Get basic rule statistics summary
    
    Returns:
        - Total rule count
        - Count by category (R0/R1/R2)
        - Brief category descriptions
    """
    try:
        rules = load_rules_from_directory()
        
        r0_count = len(rules["r0_rules"])
        r1_count = len(rules["r1_rules"])
        r2_count = len(rules["r2_rules"])
        total = r0_count + r1_count + r2_count
        
        return {
            "total_rules": total,
            "r0_count": r0_count,
            "r1_count": r1_count,
            "r2_count": r2_count,
            "categories": {
                "r0": {
                    "name": "System Hard Constraints",
                    "description": "Critical system-level rules that cannot be violated",
                    "count": r0_count
                },
                "r1": {
                    "name": "Client Hard Rules",
                    "description": "Client-specified mandatory requirements",
                    "count": r1_count
                },
                "r2": {
                    "name": "Client Soft Preferences",
                    "description": "Client preferences with weighted scoring",
                    "count": r2_count
                }
            }
        }
    except Exception as e:
        logger.error(f"Failed to load rule summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load rule summary: {str(e)}")


@router.get("/list")
async def list_all_rules():
    """
    Get list of all rules with basic information (ID, name, category)
    
    Returns simplified rule list without full descriptions for quick overview
    """
    try:
        rules = load_rules_from_directory()
        
        result = []
        
        # Process R0 rules
        for rule in rules["r0_rules"]:
            result.append({
                "id": rule["id"],
                "name": rule["name"],
                "category": "R0",
                "category_name": "System Hard"
            })
        
        # Process R1 rules
        for rule in rules["r1_rules"]:
            result.append({
                "id": rule["id"],
                "name": rule["name"],
                "category": "R1",
                "category_name": "Client Hard"
            })
        
        # Process R2 rules
        for rule in rules["r2_rules"]:
            result.append({
                "id": rule["id"],
                "name": rule["name"],
                "category": "R2",
                "category_name": "Client Soft",
                "weight": rule.get("weight", 1.0)
            })
        
        return {
            "total": len(result),
            "rules": result
        }
    except Exception as e:
        logger.error(f"Failed to load rules list: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to load rules list: {str(e)}")
