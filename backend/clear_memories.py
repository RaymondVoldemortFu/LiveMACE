"""
Standalone CLI script to clear memories without starting the backend.

Usage:
    # Clear all memories (all accounts)
    uv run python clear_memories.py

    # Clear memories for a specific account
    uv run python clear_memories.py --account 1

    # Dry run (show count without deleting)
    uv run python clear_memories.py --dry-run
"""
import argparse
import sys
import os

# Ensure backend is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.connection import SessionLocal
from database.models import AgentMemory
from config.agent_config import AgentConfig


def clear_pinecone(account_id: str = None):
    """Clear Pinecone vectors"""
    try:
        from pinecone import Pinecone
        api_key = AgentConfig.PINECONE_API_KEY
        if not api_key:
            print("  Pinecone: skipped (no API key)")
            return 0

        pc = Pinecone(api_key=api_key)
        index_name = AgentConfig.PINECONE_INDEX_NAME

        if index_name not in [idx.name for idx in pc.list_indexes()]:
            print(f"  Pinecone: index '{index_name}' not found")
            return 0

        index = pc.Index(index_name)

        if account_id:
            # Get memory IDs from SQLite first
            db = SessionLocal()
            memories = db.query(AgentMemory).filter(AgentMemory.account_id == int(account_id)).all()
            ids = [m.memory_id for m in memories]
            db.close()
            if ids:
                index.delete(ids=ids)
            print(f"  Pinecone: deleted {len(ids)} vectors")
            return len(ids)
        else:
            # Delete all - use delete_all
            index.delete(delete_all=True)
            print("  Pinecone: deleted all vectors")
            return -1
    except ImportError:
        print("  Pinecone: skipped (pinecone not installed)")
        return 0
    except Exception as e:
        print(f"  Pinecone: error - {e}")
        return 0


def clear_chroma(account_id: str = None):
    """Clear Chroma vectors"""
    try:
        import chromadb
        from chromadb.config import Settings

        persist_dir = getattr(AgentConfig, 'CHROMA_PERSIST_DIR', './chroma_db')
        if not os.path.exists(persist_dir):
            print(f"  Chroma: skipped (no data at {persist_dir})")
            return 0

        client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )

        if account_id:
            collection = client.get_or_create_collection("agent_memories")
            # Get IDs for this account
            results = collection.get(where={"account_id": str(account_id)})
            if results['ids']:
                collection.delete(ids=results['ids'])
            count = len(results['ids'])
            print(f"  Chroma: deleted {count} vectors")
            return count
        else:
            client.reset()
            print("  Chroma: reset complete")
            return -1
    except ImportError:
        print("  Chroma: skipped (chromadb not installed)")
        return 0
    except Exception as e:
        print(f"  Chroma: error - {e}")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Clear agent memories (SQLite + vector backend)")
    parser.add_argument("--account", type=str, default=None, help="Account ID to clear (default: all accounts)")
    parser.add_argument("--dry-run", action="store_true", help="Show count without deleting")
    args = parser.parse_args()

    db = SessionLocal()

    # Count
    if args.account:
        count = db.query(AgentMemory).filter(AgentMemory.account_id == int(args.account)).count()
        scope = f"account {args.account}"
    else:
        count = db.query(AgentMemory).count()
        scope = "all accounts"

    print(f"Found {count} memories for {scope}")

    if args.dry_run:
        db.close()
        return

    if count == 0 and not args.account:
        # Still try to clear vector backends even if SQLite is empty
        pass

    # Clear vector backend based on config
    backend = getattr(AgentConfig, 'MEMORY_BACKEND', 'local')
    print(f"Backend: {backend}")

    if backend == 'pinecone':
        clear_pinecone(args.account)
    elif backend == 'chroma':
        clear_chroma(args.account)

    # Clear SQLite
    if args.account:
        db.query(AgentMemory).filter(AgentMemory.account_id == int(args.account)).delete()
    else:
        db.query(AgentMemory).delete()
    db.commit()
    db.close()

    print(f"Cleared {count} memories from SQLite")
    print("Done.")


if __name__ == "__main__":
    main()
