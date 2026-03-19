# services/agent/llm_client.py
from typing import Any, Optional

from openai import OpenAI

class LLMClient:
    """
    一个极简的、可用于 Agent 的 OpenAI SDK 封装。
    完全使用 openai 库，不做自定义 HTTP 请求。
    """

    @staticmethod
    def normalize_base_url(base_url: str | None) -> str | None:
        """
        将用户输入的 endpoint 规整成 OpenAI SDK 可接受的 base_url。
        允许传入:
        - https://host/v1
        - https://host/v1/
        - https://host/v1/chat/completions
        """
        if not base_url:
            return None

        normalized = base_url.strip().rstrip("/")

        for suffix in ("/chat/completions", "/completions"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].rstrip("/")
                break

        return normalized or None

    def __init__(self, model: str, api_key: str, base_url: str = None):
        """
        model: 比如 "gpt-4.1" / "gpt-4o-mini" / "qwen2.5-72b"（你的中转平台 Model 名）
        api_key: 账户自己的 key
        base_url: 如果你用自己的 API gateway，例如 vllm / OpenAI compatible endpoint
                  直接传入，比如 "https://your-endpoint/v1"
        """
        self.model = model

        normalized_base_url = self.normalize_base_url(base_url)

        if normalized_base_url:
            # 使用自定义 endpoint（OpenAI-compatible）
            self.client = OpenAI(api_key=api_key, base_url=normalized_base_url)
        else:
            # 使用 OpenAI 官方 endpoint
            self.client = OpenAI(api_key=api_key)

    def call(self, messages, tools=None, timeout: Optional[float] = None):
        """
        统一的 LLM 调用入口，支持 tools（函数调用）
        直接返回 OpenAI 的 ChatCompletionMessage 对象，便于后续追加到 messages 历史中。
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "temperature": 0.4,
            "max_tokens": 4000,  # Increased from 800 to allow longer responses
        }
        if timeout is not None:
            kwargs["timeout"] = timeout

        response = self.client.chat.completions.create(
            **kwargs
        )

        return response.choices[0].message

    @staticmethod
    def extract_text_content(message: Any) -> str:
        """
        兼容不同 SDK/供应商响应格式，尽量提取可展示的文本。
        """
        if message is None:
            return ""

        content = getattr(message, "content", "")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
                else:
                    text = getattr(item, "text", None)
                    if text:
                        parts.append(str(text))
            return "\n".join(part.strip() for part in parts if str(part).strip())

        return str(content).strip() if content is not None else ""

    def test_connection(self, timeout_seconds: Optional[float] = 15.0) -> str:
        """
        使用与运行时一致的 OpenAI SDK 调用测试模型连通性。
        返回模型响应文本，调用失败时直接抛出异常。
        """
        message = self.call(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Reply exactly with: Connection test successful"},
            ],
            timeout=timeout_seconds,
        )
        return self.extract_text_content(message)
