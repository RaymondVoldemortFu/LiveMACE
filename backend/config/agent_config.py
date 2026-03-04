import dotenv
import os

dotenv.load_dotenv()

class AgentConfig:
    USE_AGENT = True
    AGENT_TYPE = "react"  # default agent architecture
    MAX_STEPS = 100
    STEP_REMINDER_THRESHOLD = 5

    # Docker Configuration
    DOCKER_IMAGE_NAME = "agent-sandbox:latest"
    DOCKER_POOL_SIZE = 3
    MAX_READ_CHARS = 1000  # Config for file read limit
    DOCKER_SOCKET_PATH = "unix:///var/run/docker.sock"
    DOCKERFILE_PATH = os.path.join(os.path.dirname(__file__), "../services/agent/docker")

    # Memory Configuration
    # Note: Memory is now controlled per-account via account.memory_enabled field
    # Lightweight embedding model (384 dimensions)
    MEMORY_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    # Memory backend: "local" (SQLite) or "chroma" (vector database)
    MEMORY_BACKEND = "chroma"  # Options: "local", "chroma"
    CHROMA_PERSIST_DIR = "./chroma_db"  # Directory for Chroma persistence


class LLMConfig:
    API_KEY = os.getenv("API_KEY")
    BASE_URL = os.getenv("BASE_URL")
