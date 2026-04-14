"""
Grok 与非 Grok 模型的真实 OpenAI 兼容 API 集成测试。

模型名固定：
- Grok: grok-4.20-beta-0309-reasoning
- 非 Grok: gpt-5.4

仅从环境变量读取 base_url / api_key（不使用回退，不跳过）：
- 统一中转: API_KEY / BASE_URL
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR / ".env")

from services.agent.llm_client import LLMClient

GROK_MODEL = "grok-4.20-beta-0309-reasoning"
NON_GROK_MODEL = "gpt-5.4"


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _build_grok_client() -> LLMClient:
    key = _required_env("API_KEY")
    base = _required_env("BASE_URL")
    return LLMClient(model=GROK_MODEL, api_key=key, base_url=base)


def _build_non_grok_client() -> LLMClient:
    key = _required_env("API_KEY")
    base = _required_env("BASE_URL")
    return LLMClient(model=NON_GROK_MODEL, api_key=key, base_url=base)


@pytest.fixture(scope="module")
def grok_llm() -> LLMClient:
    return _build_grok_client()


@pytest.fixture(scope="module")
def non_grok_llm() -> LLMClient:
    return _build_non_grok_client()


@pytest.mark.integration
def test_real_grok_api_connection_openai_compat(grok_llm: LLMClient):
    assert grok_llm.is_grok_model() is True
    text = grok_llm.test_connection(timeout_seconds=60.0)
    assert "connection test successful" in text.lower()


@pytest.mark.integration
def test_real_non_grok_api_connection_openai_compat(non_grok_llm: LLMClient):
    assert non_grok_llm.is_grok_model() is False
    text = non_grok_llm.test_connection(timeout_seconds=60.0)
    assert "connection test successful" in text.lower()
