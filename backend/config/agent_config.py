import dotenv
import os

dotenv.load_dotenv()

class AgentConfig:
    USE_AGENT = True
    MAX_STEPS = 4

class LLMConfig:
    API_KEY = os.getenv("API_KEY")
    BASE_URL = os.getenv("BASE_URL")
