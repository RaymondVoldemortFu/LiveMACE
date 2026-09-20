import os

from dotenv import load_dotenv
from openai import OpenAI
from openai.resources.chat.completions import Completions


load_dotenv()

WAVE3_PRODUCTION = os.getenv("WAVE3_PRODUCTION", "false").lower() == "true"
BASE_URL = os.getenv("BASE_URL" if WAVE3_PRODUCTION else "base_url")
API_KEY = os.getenv("API_KEY" if WAVE3_PRODUCTION else "api_key")
OPENAI_MODEL = (
    os.getenv("WAVE3_MODEL")
    if WAVE3_PRODUCTION
    else os.getenv("openai_model", "gpt-4o-mini")
)
REQUEST_TIMEOUT = max(1.0, float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "60")))
MAX_OUTPUT_TOKENS = max(1, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024")))


class _BoundedCompletions(Completions):
    def create(self, *args, **kwargs):
        token_key = (
            "max_completion_tokens"
            if "max_completion_tokens" in kwargs
            else "max_tokens"
        )
        kwargs[token_key] = min(
            kwargs.get(token_key) or MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS
        )
        kwargs["timeout"] = min(
            kwargs.get("timeout") or REQUEST_TIMEOUT, REQUEST_TIMEOUT
        )
        if WAVE3_PRODUCTION:
            from services.agent.llm_client import configured_extra_body
            from services.agent.request_scope import current_request_scope

            extra_body = configured_extra_body(
                kwargs.get("model", OPENAI_MODEL), kwargs.get("extra_body")
            )
            if extra_body is not None:
                kwargs["extra_body"] = extra_body
            scope = current_request_scope()
            if scope is not None:
                kwargs = scope.before_attempt(kwargs)
        return super().create(*args, **kwargs)


def get_openai_client() -> OpenAI:
    if not API_KEY:
        raise ValueError("Missing public API model credential")
    if WAVE3_PRODUCTION and (not BASE_URL or not OPENAI_MODEL):
        raise ValueError("WAVE3_MODEL and BASE_URL are required in Wave3 production")
    client = OpenAI(
        base_url=BASE_URL, api_key=API_KEY, timeout=REQUEST_TIMEOUT, max_retries=0
    )
    client.chat.completions = _BoundedCompletions(client)
    return client
