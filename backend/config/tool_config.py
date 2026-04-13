import os

import dotenv

dotenv.load_dotenv()


class ToolConfig:
    brightdata_api_key = os.getenv("BRIGHTDATA_API_KEY")
    
    # Sub-agent configuration
    MAX_SEARCH_STEPS = 5
    
    # Context management - maximum tokens before triggering summarization
    # Default: 128000 (can be overridden via MAX_CONTEXT_TOKENS in .env)
    MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "128000"))

    # Search sub-agent network robustness configuration
    SEARCH_AGENT_MAX_RETRIES = max(0, int(os.getenv("SEARCH_AGENT_MAX_RETRIES", "3")))
    SEARCH_AGENT_SEARCH_TIMEOUT_SECONDS = max(
        1.0, float(os.getenv("SEARCH_AGENT_SEARCH_TIMEOUT_SECONDS", "20"))
    )
    SEARCH_AGENT_UNLOCKER_TIMEOUT_SECONDS = max(
        1.0, float(os.getenv("SEARCH_AGENT_UNLOCKER_TIMEOUT_SECONDS", "30"))
    )
    SEARCH_AGENT_LOCAL_FETCH_CONNECT_TIMEOUT_SECONDS = max(
        1.0, float(os.getenv("SEARCH_AGENT_LOCAL_FETCH_CONNECT_TIMEOUT_SECONDS", "5"))
    )
    SEARCH_AGENT_LOCAL_FETCH_READ_TIMEOUT_SECONDS = max(
        1.0, float(os.getenv("SEARCH_AGENT_LOCAL_FETCH_READ_TIMEOUT_SECONDS", "20"))
    )
    