import dotenv
import os

dotenv.load_dotenv()

class AgentConfig:
    USE_AGENT = True
    AGENT_TYPE = "react"  # default agent architecture
    MAX_STEPS = 100
    STEP_REMINDER_THRESHOLD = 5
    # System prompt section switches:
    # - True: include SIMULATION ENVIRONMENT NOTICE block
    # - False: omit this block from trading system prompt
    AGENT_INCLUDE_SIMULATION_NOTICE = os.getenv("AGENT_INCLUDE_SIMULATION_NOTICE", "false") == "true"
    # Tool routing behavior:
    # - True: agent should call `select_tools` to dynamically route tools by step
    # - False: legacy mode without dynamic tool routing instructions
    AGENT_ENABLE_TOOL_ROUTING = os.getenv("AGENT_ENABLE_TOOL_ROUTING", "true").strip().lower() in {"1", "true", "yes", "on"}

    TOOL_SELECTOR_TOP_K = 30
    TOOL_SELECTOR_MAX_RETRIES = 10
    TOOL_SELECTOR_MIN_EXTRA = 1
    TOOL_CALL_DUP_MAX = 5
    TOOL_CALL_DUP_WARN = 10

    # Docker Configuration
    DOCKER_IMAGE_NAME = "agent-sandbox:latest"
    # Minimum baseline pool size
    DOCKER_POOL_SIZE = 3
    # Dynamic pool sizing based on active AI accounts
    DOCKER_POOL_DYNAMIC_BY_ACTIVE_ACCOUNTS = os.getenv("DOCKER_POOL_DYNAMIC_BY_ACTIVE_ACCOUNTS", "true").strip().lower() in {"1", "true", "yes", "on"}
    # Optional hard cap for dynamic base pool (0 means no cap)
    DOCKER_POOL_MAX_SIZE = int(os.getenv("DOCKER_POOL_MAX_SIZE", "0"))
    DOCKER_POOL_MAX_OVERFLOW = 3
    DOCKER_POOL_LEASE_TIMEOUT_SECONDS = 30
    MAX_READ_CHARS = 1000  # Config for file read limit
    DOCKER_SOCKET_PATH = "unix:///var/run/docker.sock"
    DOCKERFILE_PATH = os.path.join(os.path.dirname(__file__), "../services/agent/docker")

    # Agent execution concurrency
    AGENT_MAX_CONCURRENCY = max(1, int(os.getenv("AGENT_MAX_CONCURRENCY", "25")))

    # US stock data source behavior (Alpaca)
    # True: force feed=IEX
    # False: do not pass feed argument (Alpaca default routing)
    ALPACA_USE_IEX_FEED = os.getenv("ALPACA_USE_IEX_FEED", "true").strip().lower() in {"1", "true", "yes", "on"}

    # Memory Configuration
    # Note: Memory is now controlled per-account via account.memory_enabled field
    # Lightweight embedding model (384 dimensions)
    MEMORY_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"
    # Memory backend: "local" (SQLite), "chroma" (vector database), or "pinecone" (cloud)
    MEMORY_BACKEND = "pinecone"  # Options: "local", "chroma", "pinecone"
    CHROMA_PERSIST_DIR = "./chroma_db"  # Directory for Chroma persistence
    # Pinecone Configuration
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
    PINECONE_INDEX_NAME = "agent-memories"
    PINECONE_ENVIRONMENT = "us-east-1"  # Free tier region
    # Similarity threshold for counting as effective retrieval (0.0-1.0)
    MEMORY_RETRIEVAL_THRESHOLD = 0.6
    # Rerank: over-fetch top_k candidates, then rerank by score + time decay to get final limit
    MEMORY_RERANK_TOP_K = 20
    # Time decay half-life in days (memories older than this get 50% weight)
    MEMORY_TIME_DECAY_HALF_LIFE_DAYS = 7
    # Rerank formula: α × similarity + (1-α) × time_decay
    MEMORY_RERANK_SIMILARITY_WEIGHT = 0.8


class LLMConfig:
    API_KEY = os.getenv("API_KEY")
    BASE_URL = os.getenv("BASE_URL")
