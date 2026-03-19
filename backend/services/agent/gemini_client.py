# services/agent/gemini_client.py
"""Gemini API client adapter for LLMClient interface"""
import json
import logging
import requests
from typing import Any, List, Dict, Optional

logger = logging.getLogger(__name__)


class GeminiMessage:
    """Adapter to make Gemini response compatible with OpenAI message format"""
    def __init__(self, content: str, tool_calls: Optional[List] = None, thought_signatures: Optional[List[str]] = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.thought_signatures = thought_signatures or []

    def model_dump(self):
        result = {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [tc.model_dump() if hasattr(tc, 'model_dump') else tc for tc in self.tool_calls]
        }
        # Store thought signatures for later retrieval
        if self.thought_signatures:
            result["_gemini_thought_signatures"] = self.thought_signatures
        return result

    def dict(self):
        return self.model_dump()


class GeminiToolCall:
    """Adapter for Gemini function call to OpenAI format"""
    def __init__(self, call_id: str, name: str, arguments: str, thought_signature: Optional[str] = None):
        self.id = call_id
        self.type = "function"
        self.function = GeminiFunction(name, arguments)
        self.thought_signature = thought_signature

    def model_dump(self):
        result = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.function.name,
                "arguments": self.function.arguments
            }
        }
        if self.thought_signature:
            result["_thought_signature"] = self.thought_signature
        return result


class GeminiFunction:
    """Adapter for function call details"""
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class GeminiClient:
    """Gemini API client using HTTP requests (supports custom base_url)"""

    def __init__(self, model: str, api_key: str, base_url: str = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip('/') if base_url else "https://generativelanguage.googleapis.com/v1beta"
        self.thought_signatures = []  # Track thought signatures across turns

    def _convert_tools_to_gemini(self, tools: Optional[List[Dict]]) -> Optional[List]:
        """Convert OpenAI tool format to Gemini function declarations"""
        if not tools:
            return None

        gemini_tools = []
        for tool in tools:
            if tool.get("type") == "function":
                func = tool["function"]
                gemini_tools.append({
                    "name": func["name"],
                    "description": func.get("description", ""),
                    "parameters": func.get("parameters", {})
                })

        return gemini_tools if gemini_tools else None

    def _convert_messages_to_gemini(self, messages: List[Dict]) -> tuple:
        """Convert OpenAI messages to Gemini format with thought signature tracking"""
        system_instruction = None
        contents = []

        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
                i += 1
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": content}]})
                i += 1
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    parts = []
                    msg_signatures = msg.get("_gemini_thought_signatures", [])

                    for idx, tc in enumerate(tool_calls):
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except json.JSONDecodeError:
                            logger.debug(f"Failed to parse tool arguments for {func.get('name')}, using empty args")
                            args = {}
                        part = {
                            "functionCall": {
                                "name": func.get("name"),
                                "args": args
                            }
                        }
                        ts = tc.get("_thought_signature") or (msg_signatures[idx] if idx < len(msg_signatures) else None)
                        if ts:
                            part["thoughtSignature"] = ts
                        parts.append(part)
                    contents.append({"role": "model", "parts": parts})
                elif content:
                    contents.append({"role": "model", "parts": [{"text": content}]})
                i += 1
            elif role == "tool":
                # Collect all consecutive tool messages into one user message
                function_responses = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tool_msg = messages[i]
                    tool_name = tool_msg.get("name", "unknown")
                    tool_content = tool_msg.get("content", "")
                    function_responses.append({
                        "functionResponse": {
                            "name": tool_name,
                            "response": {"result": tool_content}
                        }
                    })
                    i += 1
                contents.append({"role": "user", "parts": function_responses})
            else:
                i += 1

        return system_instruction, contents

    def call(self, messages: List[Dict], tools: Optional[List[Dict]] = None) -> GeminiMessage:
        """Call Gemini API via HTTP with thought signature support"""
        system_instruction, contents = self._convert_messages_to_gemini(messages)
        gemini_tools = self._convert_tools_to_gemini(tools)

        # Build request payload
        payload = {}

        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        payload["contents"] = contents

        if gemini_tools:
            payload["tools"] = [{"functionDeclarations": gemini_tools}]

        payload["generationConfig"] = {
            "temperature": 0.4,
            "maxOutputTokens": 4000
        }

        # Make HTTP request
        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json"}

        # Add API key (query param for Google, header for proxies)
        if "generativelanguage.googleapis.com" in self.base_url:
            url += f"?key={self.api_key}"
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Log request for debugging (redact API key)
        safe_url = f"{self.base_url}/models/{self.model}:generateContent"
        logger.debug(f"Gemini API request to {safe_url}")
        logger.debug(f"Gemini request payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")

        response = requests.post(url, headers=headers, json=payload, timeout=30)

        # Log error details for debugging (redact URL to avoid leaking API key)
        if response.status_code != 200:
            logger.error(f"Gemini API error {response.status_code}: {response.text}")

        try:
            response.raise_for_status()
        except Exception:
            raise Exception(f"Gemini API error {response.status_code}: {response.text}") from None
        result = response.json()

        # Parse response
        text_parts = []
        tool_calls = []
        thought_signatures = []

        if "candidates" in result and result["candidates"]:
            candidate = result["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                for part in candidate["content"]["parts"]:
                    if "text" in part:
                        text_parts.append(part["text"])
                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        thought_sig = part.get("thoughtSignature")
                        tool_calls.append(GeminiToolCall(
                            call_id=f"call_{fc['name']}_{len(tool_calls)}",
                            name=fc["name"],
                            arguments=json.dumps(fc.get("args", {})),
                            thought_signature=thought_sig
                        ))
                        # Extract thought signature
                        if thought_sig:
                            thought_signatures.append(thought_sig)

        return GeminiMessage("\n".join(text_parts), tool_calls, thought_signatures)

    @staticmethod
    def extract_text_content(message: Any) -> str:
        """Extract text from message"""
        if isinstance(message, GeminiMessage):
            return message.content
        if hasattr(message, "content"):
            return str(message.content)
        return str(message)

    def test_connection(self) -> str:
        """Test Gemini API connection"""
        message = self.call(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Reply exactly with: Connection test successful"},
            ]
        )
        return self.extract_text_content(message)
