import sys
import os

# Add current directory to path so imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.connection import engine, Base
from database.models import (
    AgentTrace, AIDecisionLog, Account, AgentMemory,
    AccountSnapshot, AssetMetadata, RuleEvaluationResult
)
from sqlalchemy import text

def update_schema():
    print("Updating database schema...")
    
    # 1. Create all tables (AgentTrace, AgentMemory, AccountSnapshot, AssetMetadata, RuleEvaluationResult, etc.) if they don't exist
    print("Creating tables if not exist...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully (including account_snapshots, asset_metadata, rule_evaluation_results)")
    
    with engine.connect() as conn:
        try:
            # 2. Add trace_id column to AIDecisionLog if it doesn't exist
            print("Checking for trace_id column in ai_decision_logs...")
            result = conn.execute(text("PRAGMA table_info(ai_decision_logs)"))
            columns = [row[1] for row in result]
            
            if "trace_id" not in columns:
                print("Adding trace_id column to ai_decision_logs...")
                conn.execute(text("ALTER TABLE ai_decision_logs ADD COLUMN trace_id VARCHAR(36)"))
                print("Column trace_id added.")
            else:
                print("Column trace_id already exists.")

            # 3. Add agent_type column to Accounts if it doesn't exist
            print("Checking for agent_type column in accounts...")
            result = conn.execute(text("PRAGMA table_info(accounts)"))
            columns = [row[1] for row in result]
            
            if "agent_type" not in columns:
                print("Adding agent_type column to accounts...")
                conn.execute(text("ALTER TABLE accounts ADD COLUMN agent_type VARCHAR(20) DEFAULT 'react' NOT NULL"))
                print("Column agent_type added.")
            else:
                print("Column agent_type already exists.")
            
            # 4. Add enable_rule_aware column to Accounts if it doesn't exist
            print("\nChecking for enable_rule_aware column in accounts...")
            result = conn.execute(text("PRAGMA table_info(accounts)"))
            columns = [row[1] for row in result]
            
            if "enable_rule_aware" not in columns:
                print("Adding enable_rule_aware column to accounts...")
                conn.execute(text("ALTER TABLE accounts ADD COLUMN enable_rule_aware VARCHAR(10) DEFAULT 'false' NOT NULL"))
                print("Column enable_rule_aware added.")
            else:
                print("Column enable_rule_aware already exists.")
            
            # 5. Remove old LLM audit fields from Accounts if they exist (moved to RuleEvaluationResult)
            print("\nChecking for deprecated LLM audit fields in accounts...")
            result = conn.execute(text("PRAGMA table_info(accounts)"))
            columns = [row[1] for row in result]
            
            deprecated_fields = ["llm_audit_count", "llm_audit_avg_score", "llm_audit_avg_coverage", "llm_audit_avg_conflict"]
            has_deprecated = any(field in columns for field in deprecated_fields)
            
            if has_deprecated:
                print("Found deprecated LLM audit fields in accounts table.")
                print("Note: SQLite does not support DROP COLUMN directly.")
                print("These fields are now in rule_evaluation_results table (per-decision).")
                print("If you want to remove them, you need to recreate the table.")
            
            # 6. Add LLM audit detail fields to RuleEvaluationResult if they don't exist
            print("\nChecking for LLM audit detail fields in rule_evaluation_results...")
            result = conn.execute(text("PRAGMA table_info(rule_evaluation_results)"))
            columns = [row[1] for row in result]
            
            llm_audit_detail_fields = {
                "llm_audit_score": "REAL",
                "llm_audit_coverage": "REAL",
                "llm_audit_conflict": "REAL",
                "llm_audit_json": "TEXT"
            }
            
            for field_name, field_type in llm_audit_detail_fields.items():
                if field_name not in columns:
                    print(f"Adding {field_name} column to rule_evaluation_results...")
                    conn.execute(text(f"ALTER TABLE rule_evaluation_results ADD COLUMN {field_name} {field_type}"))
                    print(f"Column {field_name} added.")
                else:
                    print(f"Column {field_name} already exists.")
            
            # 7. Verify new tables exist
            print("\nVerifying new tables...")
            tables_to_check = ['account_snapshots', 'asset_metadata', 'rule_evaluation_results']
            for table_name in tables_to_check:
                result = conn.execute(text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'"))
                if result.fetchone():
                    print(f"✓ Table {table_name} exists")
                else:
                    print(f"✗ Table {table_name} not found")

            # 4. Add volatility column to AgentPeriodCheckpoint if it doesn't exist
            print("Checking for volatility column in agent_period_checkpoints...")
            result = conn.execute(text("PRAGMA table_info(agent_period_checkpoints)"))
            columns = [row[1] for row in result]

            if "volatility" not in columns:
                print("Adding volatility column to agent_period_checkpoints...")
                conn.execute(text("ALTER TABLE agent_period_checkpoints ADD COLUMN volatility FLOAT DEFAULT 0.0 NOT NULL"))
                print("Column volatility added.")
            else:
                print("Column volatility already exists.")
                
        except Exception as e:
            print(f"Error updating schema: {e}")

if __name__ == "__main__":
    update_schema()

