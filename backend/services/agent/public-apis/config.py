import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

BASE_URL = os.getenv("base_url")
API_KEY = os.getenv("api_key")
OPENAI_MODEL = os.getenv("openai_model", "gpt-4o-mini")


def get_openai_client() -> OpenAI:
    if not API_KEY:
        raise ValueError("Missing api_key in .env")
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)
