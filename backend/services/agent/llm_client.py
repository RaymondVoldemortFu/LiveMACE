# services/agent/llm_client.py
from openai import OpenAI

class LLMClient:
    """
    一个极简的、可用于 Agent 的 OpenAI SDK 封装。
    完全使用 openai 库，不做自定义 HTTP 请求。
    """

    def __init__(self, model: str, api_key: str, base_url: str = None):
        """
        model: 比如 "gpt-4.1" / "gpt-4o-mini" / "qwen2.5-72b"（你的中转平台 Model 名）
        api_key: 账户自己的 key
        base_url: 如果你用自己的 API gateway，例如 vllm / OpenAI compatible endpoint
                  直接传入，比如 "https://your-endpoint/v1"
        """
        self.model = model

        if base_url:
            # 使用自定义 endpoint（OpenAI-compatible）
            self.client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            # 使用 OpenAI 官方 endpoint
            self.client = OpenAI(api_key=api_key)

    def call(self, messages, tools=None):
        """
        统一的 LLM 调用入口，支持 tools（函数调用）
        返回格式自动规整为:
        {
            "content": "...",
            "tool_calls": [...]
        }
        """

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            temperature=0.4,
            max_tokens=800,
        )

        msg = response.choices[0].message

        return {
            "content": msg.content or "",
            "tool_calls": msg.tool_calls or [],
        }
