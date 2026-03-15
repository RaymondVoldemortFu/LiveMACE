"""
Rule Engine - Core rule management and parsing system
"""
import json
import logging
from enum import Enum
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class RuleLevel(Enum):
    """Rule priority levels"""
    R0_SYSTEM_HARD = 0      # System/Exchange hard constraints (highest priority)
    R1_CLIENT_HARD = 1      # Client hard policies
    R2_CLIENT_SOFT = 2      # Client soft preferences (lowest priority)


@dataclass
class Rule:
    """Individual rule definition"""
    id: str                     # e.g., "R0-01", "R1-02"
    level: RuleLevel            # Rule priority level
    name: str                   # Short name
    description: str            # Full description
    enforcement: str            # How it's enforced
    parameters: Dict[str, Any]  # Configurable parameters (thresholds, limits, etc.)
    
    def __post_init__(self):
        """Validate rule ID format"""
        if not self.id.startswith(('R0-', 'R1-', 'R2-')):
            raise ValueError(f"Invalid rule ID format: {self.id}")
    
    def get_priority(self) -> int:
        """Get numeric priority (lower is higher priority)"""
        return self.level.value


class RuleEngine:
    """
    Central rule management system
    Loads, parses, and provides access to trading rules
    """
    
    def __init__(self, rule_documents_path: Optional[str] = None):
        """
        Initialize rule engine
        
        Args:
            rule_documents_path: Path to directory containing rule documents
        """
        self.rules: Dict[str, Rule] = {}
        self.rules_by_level: Dict[RuleLevel, List[Rule]] = {
            RuleLevel.R0_SYSTEM_HARD: [],
            RuleLevel.R1_CLIENT_HARD: [],
            RuleLevel.R2_CLIENT_SOFT: []
        }
        
        if rule_documents_path:
            self.load_rules_from_path(rule_documents_path)
    
    def load_rules_from_path(self, path: str):
        """Load all rule documents from a directory"""
        rule_path = Path(path)
        if not rule_path.exists():
            logger.warning(f"Rule documents path does not exist: {path}")
            return
        
        # Load JSON rule files
        for rule_file in rule_path.glob("*.json"):
            try:
                self.load_rule_document(str(rule_file))
            except Exception as e:
                logger.error(f"Failed to load rule file {rule_file}: {e}")
    
    def load_rule_document(self, filepath: str):
        """Load a single rule document (JSON format)"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for rule_data in data.get('rules', []):
            try:
                rule = self._parse_rule(rule_data)
                self.add_rule(rule)
            except Exception as e:
                logger.error(f"Failed to parse rule: {rule_data.get('id', 'unknown')}: {e}")
    
    def _parse_rule(self, rule_data: Dict[str, Any]) -> Rule:
        """Parse rule data into Rule object"""
        rule_id = rule_data['id']
        
        # Determine level from ID prefix
        if rule_id.startswith('R0-'):
            level = RuleLevel.R0_SYSTEM_HARD
        elif rule_id.startswith('R1-'):
            level = RuleLevel.R1_CLIENT_HARD
        elif rule_id.startswith('R2-'):
            level = RuleLevel.R2_CLIENT_SOFT
        else:
            raise ValueError(f"Invalid rule ID prefix: {rule_id}")
        
        return Rule(
            id=rule_id,
            level=level,
            name=rule_data.get('name', ''),
            description=rule_data.get('description', ''),
            enforcement=rule_data.get('enforcement', ''),
            parameters=rule_data.get('parameters', {})
        )
    
    def add_rule(self, rule: Rule):
        """Add a rule to the engine"""
        self.rules[rule.id] = rule
        self.rules_by_level[rule.level].append(rule)
        logger.info(f"Loaded rule: {rule.id} - {rule.name}")
    
    def get_rule(self, rule_id: str) -> Optional[Rule]:
        """Get a specific rule by ID"""
        return self.rules.get(rule_id)
    
    def get_rules_by_level(self, level: RuleLevel) -> List[Rule]:
        """Get all rules at a specific level"""
        return self.rules_by_level.get(level, [])
    
    def get_all_rules(self) -> List[Rule]:
        """Get all rules sorted by priority"""
        all_rules = []
        for level in [RuleLevel.R0_SYSTEM_HARD, RuleLevel.R1_CLIENT_HARD, RuleLevel.R2_CLIENT_SOFT]:
            all_rules.extend(self.rules_by_level[level])
        return all_rules
    
    def format_rules_for_prompt(self) -> str:
        """
        Format all rules as text for LLM prompt
        Returns a structured markdown-style document
        """
        sections = []
        
        # R0 Rules
        r0_rules = self.get_rules_by_level(RuleLevel.R0_SYSTEM_HARD)
        if r0_rules:
            sections.append("## R0: System & Exchange Hard Rules (CRITICAL - Cannot be violated)")
            for rule in r0_rules:
                sections.append(f"\n### {rule.id}: {rule.name}")
                sections.append(f"**Description:** {rule.description}")
                sections.append(f"**Enforcement:** {rule.enforcement}")
                if rule.parameters:
                    sections.append(f"**Parameters:** {json.dumps(rule.parameters, indent=2)}")
        
        # R1 Rules
        r1_rules = self.get_rules_by_level(RuleLevel.R1_CLIENT_HARD)
        if r1_rules:
            sections.append("\n## R1: Client Mandates - Hard Policies (MANDATORY)")
            for rule in r1_rules:
                sections.append(f"\n### {rule.id}: {rule.name}")
                sections.append(f"**Description:** {rule.description}")
                sections.append(f"**Enforcement:** {rule.enforcement}")
                if rule.parameters:
                    sections.append(f"**Parameters:** {json.dumps(rule.parameters, indent=2)}")
        
        # R2 Rules
        r2_rules = self.get_rules_by_level(RuleLevel.R2_CLIENT_SOFT)
        if r2_rules:
            sections.append("\n## R2: Client Preferences - Soft Rules (ADVISORY)")
            for rule in r2_rules:
                sections.append(f"\n### {rule.id}: {rule.name}")
                sections.append(f"**Description:** {rule.description}")
                sections.append(f"**Trade-off Logic:** {rule.enforcement}")
                if rule.parameters:
                    sections.append(f"**Parameters:** {json.dumps(rule.parameters, indent=2)}")
        
        return "\n".join(sections)
    
    def get_rule_summary(self) -> Dict[str, int]:
        """Get summary of loaded rules"""
        return {
            "R0_count": len(self.rules_by_level[RuleLevel.R0_SYSTEM_HARD]),
            "R1_count": len(self.rules_by_level[RuleLevel.R1_CLIENT_HARD]),
            "R2_count": len(self.rules_by_level[RuleLevel.R2_CLIENT_SOFT]),
            "total": len(self.rules)
        }
