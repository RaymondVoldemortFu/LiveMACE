import os

import dotenv

dotenv.load_dotenv()


class ToolConfig:
    tavily_api_key = os.getenv("TAVILY_API_KEY")
    RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
    TOOLENV_ROOT = r"backend/services/agent/toolenv"
    TOOLENV_TOOLS_ROOT = r"backend/services/agent/toolenv/tools"
    TOOLENV_SCHEMA_ROOT = os.getenv("TOOLENV_SCHEMA_ROOT")
    TOOLENV_BLACKLIST = os.getenv("TOOLENV_BLACKLIST")
    
    # Sub-agent configuration
    MAX_SEARCH_STEPS = 5