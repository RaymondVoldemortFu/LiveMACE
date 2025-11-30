import os

import dotenv

dotenv.load_dotenv()


class ToolConfig:
    tavily_api_key = os.getenv("TAVILY_API_KEY")
    
    # Sub-agent configuration
    MAX_SEARCH_STEPS = 5