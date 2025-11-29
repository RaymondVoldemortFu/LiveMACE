import dotenv
import os

dotenv.load_dotenv()

class AgentConfig:
    USE_AGENT = True
    MAX_STEPS = 20
    STEP_REMINDER_THRESHOLD = 5

class LLMConfig:
    API_KEY = os.getenv("API_KEY")
    BASE_URL = os.getenv("BASE_URL")
