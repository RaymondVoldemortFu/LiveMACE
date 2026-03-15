import os

import dotenv

dotenv.load_dotenv()


class ToolConfig:
    tavily_api_key = os.getenv("TAVILY_API_KEY")
    
    # Sub-agent configuration
    MAX_SEARCH_STEPS = 5
    
    # Context management - maximum tokens before triggering summarization
    # Default: 128000 (can be overridden via MAX_CONTEXT_TOKENS in .env)
    MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "128000"))