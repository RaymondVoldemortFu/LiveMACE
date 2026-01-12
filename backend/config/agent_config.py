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
    MEMORY_ENABLED = False
    MEMORY_PROVIDER = "mem0"
    MEMORY_VECTOR_PROVIDER = "aliyun"
    MEMORY_EMBEDDING_MODEL = "text-embedding-v3" # Example model name, update as needed
    MEMORY_COLLECTION_NAME = "agent_memories"
    
    # Custom Extraction LLM Configuration
    MEMORY_LLM_PROVIDER = "openai" # or custom
    MEMORY_LLM_MODEL = "gpt-4o"
    MEMORY_LLM_API_KEY = os.getenv("MEMORY_LLM_API_KEY", os.getenv("API_KEY"))
    MEMORY_LLM_BASE_URL = os.getenv("MEMORY_LLM_BASE_URL", os.getenv("BASE_URL"))


class LLMConfig:
    API_KEY = os.getenv("API_KEY")
    BASE_URL = os.getenv("BASE_URL")
