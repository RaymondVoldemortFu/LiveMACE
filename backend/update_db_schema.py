import sys
import os

# Add current directory to path so imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.connection import engine, Base
from database.models import AgentTrace, AIDecisionLog, Account, AgentMemory, AgentPeriodCheckpoint
from sqlalchemy import text

def update_schema():
    print("Updating database schema...")
    
    # 1. Create all tables (AgentTrace, AgentMemory, etc.) if they don't exist
    print("Creating tables if not exist...")
    Base.metadata.create_all(bind=engine)
    
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
                
        except Exception as e:
            print(f"Error updating schema: {e}")

if __name__ == "__main__":
    update_schema()

