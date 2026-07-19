# services/agent/llm_client.py
from __future__ import annotations

import base64
import copy
import json
import logging
import os
import time
from typing import Any, List, Optional, Sequence

import httpx
from openai import OpenAI
from openai.types.chat.chat_completion_message import ChatCompletionMessage

# Gemini 经部分兼容网关时：并行 functionCall 往往只在第一个 part 带 thought_signature，
# 回传时若后续 part 缺失，上游会 400（如 position 2 / get_account_state）。
_THOUGHT_SIG_KEYS: tuple[str, ...] = ("thought_signature", "thoughtSignature")

# 部分兼容网关在响应 JSON 中不带 thought_signature，但回传历史时要求 function 上存在且非空；
# 空串或非 base64 形态可能被上游判为「缺失」（错误信息仍写 missing）。
_DEFAULT_GEMINI_THOUGHT_SIG_PLACEHOLDER = base64.b64encode(
    b"open_alpha_arena_gemini_thought_sig_compat_v1"
).decode("ascii")

logger = logging.getLogger(__name__)
llm_client_logger = logging.getLogger("llm_client")
_DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS = 20 * 60


class LLMClient:
    MAX_TOOL_CALLS_PER_ASSISTANT_TURN = 20
    THINKING_TIMEOUT_GUARDRAIL_SECONDS = 10 * 60

    """
    可用于 Agent 的 OpenAI SDK 封装。
    所有模型（含名称中带 gemini、经 OpenAI 兼容网关转发的情形）均走 chat.completions。
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

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = None,
        extra_body: Optional[dict[str, Any]] = None,
        reasoning_effort: Optional[str] = None,
    ):
        """
        model: 比如 "gpt-4.1" / "gemini-2.0-flash"（经兼容网关）
        api_key: 账户自己的 key
        base_url: OpenAI 兼容 gateway，例如 "https://your-endpoint/v1"
        """
        self.model = model
        self.extra_body = copy.deepcopy(extra_body) if extra_body else None
        self.reasoning_effort = reasoning_effort
        normalized_base_url = self.normalize_base_url(base_url)
        # OpenAI SDK 解析响应时会丢掉 ChatCompletionMessageFunctionToolCall / Function 上未在 schema 声明的字段，
        # 部分 Gemini 网关把 thought_signature 放在原始 JSON 里；用 httpx 钩子抓取 wire 层 tool_calls 供回传合并。
        self._last_wire_tool_calls: list[dict[str, Any]] | None = None
        http_client: httpx.Client | None = None
        if self.is_gemini_model():

            def _on_response(response: httpx.Response) -> None:
                try:
                    if response.request.method != "POST":
                        return
                    if "/chat/completions" not in str(response.request.url):
                        return
                    ct = (response.headers.get("content-type") or "").lower()
                    if "event-stream" in ct or "text/event-stream" in ct:
                        return
                    response.read()
                    data = response.json()
                    msg = (data.get("choices") or [{}])[0].get("message") or {}
                    raw_tcs = msg.get("tool_calls")
                    if isinstance(raw_tcs, list) and raw_tcs:
                        self._last_wire_tool_calls = [x for x in raw_tcs if isinstance(x, dict)]
                    else:
                        self._last_wire_tool_calls = None
                except Exception:
                    self._last_wire_tool_calls = None

            http_client = httpx.Client(event_hooks={"response": [_on_response]})

        if normalized_base_url:
            self.client = OpenAI(
                api_key=api_key,
                base_url=normalized_base_url,
                http_client=http_client,
            )
        else:
            self.client = OpenAI(api_key=api_key, http_client=http_client)

        # Gemini 路径使用自定义 httpx.Client；OpenAI() 会持有其引用，须通过 client.close() 释放连接。
        self._closed = False
        # Retry count for transient upstream/provider errors.
        self.max_retries = max(0, int(os.getenv("LLM_REQUEST_MAX_RETRIES", "2")))
        self.default_timeout_seconds = float(
            os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", str(_DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS))
        )
        self._long_timeout_strikes = 0
        self._last_long_timeout_elapsed_ms: Optional[int] = None

    def close(self) -> None:
        """关闭底层 HTTP 客户端（含自定义 httpx.Client）。长驻进程在丢弃 LLMClient 前应调用，避免套接字泄漏。"""
        if self._closed:
            return
        try:
            self.client.close()
        except Exception as e:
            logger.warning("LLMClient.close() failed: %s", e, exc_info=True)
        finally:
            self._closed = True

    def __enter__(self) -> LLMClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def is_gemini_model_name(model: str | None) -> bool:
        return "gemini" in (model or "").strip().lower()

    @staticmethod
    def is_grok_model_name(model: str | None) -> bool:
        return "grok" in (model or "").strip().lower()

    def is_gemini_model(self) -> bool:
        return self.is_gemini_model_name(self.model)

    def is_grok_model(self) -> bool:
        return self.is_grok_model_name(self.model)

    @staticmethod
    def _gemini_thought_signature_placeholder_value() -> str:
        custom = (os.getenv("GEMINI_THOUGHT_SIGNATURE_PLACEHOLDER") or "").strip()
        return custom if custom else _DEFAULT_GEMINI_THOUGHT_SIG_PLACEHOLDER

    @staticmethod
    def _collapse_json_schema_union_types_for_gemini(node: Any) -> None:
        """
        Gemini 经部分网关转 Proto 时，properties 里 JSON Schema 的 type 不能是 list（如 ["number","string"]），
        否则会 400：Proto field is not repeating, cannot start list。
        """
        if isinstance(node, dict):
            t = node.get("type")
            if isinstance(t, list):
                # 优先选数值类型，避免 ["number","string"] 误选 string 丢失语义
                for p in ("number", "integer", "boolean", "string", "array", "object"):
                    if p in t:
                        node["type"] = p
                        break
                else:
                    node["type"] = "string"
            for v in node.values():
                LLMClient._collapse_json_schema_union_types_for_gemini(v)
        elif isinstance(node, list):
            for item in node:
                LLMClient._collapse_json_schema_union_types_for_gemini(item)

    @staticmethod
    def _sanitize_openai_tools_for_gemini(tools: Sequence[Any]) -> list[Any]:
        """深拷贝并修正 tools，避免修改 registry 内 parameters 引用。"""
        out = copy.deepcopy(list(tools))
        for entry in out:
            if not isinstance(entry, dict):
                continue
            fn = entry.get("function")
            if isinstance(fn, dict):
                params = fn.get("parameters")
                if isinstance(params, dict):
                    LLMClient._collapse_json_schema_union_types_for_gemini(params)
        return out

    def call(
        self,
        messages,
        tools=None,
        timeout: Optional[float] = None,
        response_format: Optional[dict] = None,
    ):
        """
        统一的 LLM 调用入口，支持 tools（函数调用）
        直接返回 ChatCompletionMessage，便于后续追加到 messages 历史中。
        """
        self._last_wire_tool_calls = None
        self._last_long_timeout_elapsed_ms = None
        request_kwargs = {
            "model": self.model,
            "messages": self._normalize_messages_for_api(messages, model=self.model),
            "tools": tools,
            "temperature": 0.4,
        }
        if self.is_grok_model():
            request_kwargs["max_completion_tokens"] = 4000
        else:
            request_kwargs["max_tokens"] = 4000
        request_kwargs["timeout"] = self.default_timeout_seconds if timeout is None else timeout
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        if self.reasoning_effort:
            request_kwargs["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            request_kwargs["extra_body"] = copy.deepcopy(self.extra_body)

        # 部分 Gemini 兼容网关在并行 functionCall 上只对首条下发可校验的 thought_signature；
        # 关闭并行工具输出，迫使模型逐条发起调用，避免后续 part 缺签导致 400。
        if self.is_gemini_model():
            request_kwargs["parallel_tool_calls"] = False
            if tools:
                request_kwargs["tools"] = self._sanitize_openai_tools_for_gemini(tools)

        tool_count = len(tools) if isinstance(tools, (list, tuple)) else 0
        message_count = len(request_kwargs.get("messages") or [])
        call_id = f"{int(time.time() * 1000)}-{id(self)}"
        llm_client_logger.info(
            "llm_call_start call_id=%s model=%s message_count=%s tool_count=%s timeout=%s response_format=%s",
            call_id,
            self.model,
            message_count,
            tool_count,
            request_kwargs.get("timeout"),
            bool(response_format),
        )
        try:
            response = self._create_with_retry(
                request_kwargs,
                call_id=call_id,
                message_count=message_count,
                tool_count=tool_count,
            )
        except Exception:
            if self._last_long_timeout_elapsed_ms is not None:
                return self._build_timeout_guardrail_message(
                    elapsed_ms=self._last_long_timeout_elapsed_ms,
                    call_id=call_id,
                )
            raise

        return response.choices[0].message

    def _create_with_retry(
        self,
        request_kwargs: dict[str, Any],
        *,
        call_id: str,
        message_count: int,
        tool_count: int,
    ):
        attempts = self.max_retries + 1
        last_err: Exception | None = None
        call_start = time.perf_counter()
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.chat.completions.create(**request_kwargs)
                elapsed_ms = int((time.perf_counter() - call_start) * 1000)
                llm_client_logger.info(
                    "llm_call_success call_id=%s model=%s attempt=%s/%s elapsed_ms=%s message_count=%s tool_count=%s",
                    call_id,
                    self.model,
                    attempt,
                    attempts,
                    elapsed_ms,
                    message_count,
                    tool_count,
                )
                return response
            except Exception as err:
                last_err = err
                elapsed_ms = int((time.perf_counter() - call_start) * 1000)
                llm_client_logger.warning(
                    "llm_call_attempt_failed call_id=%s model=%s attempt=%s/%s elapsed_ms=%s error=%s",
                    call_id,
                    self.model,
                    attempt,
                    attempts,
                    elapsed_ms,
                    str(err),
                )
                if attempt >= attempts:
                    break
                logger.warning(
                    "LLM request failed (attempt %s/%s), retrying: %s",
                    attempt,
                    attempts,
                    err,
                )
                time.sleep(min(1.0, 0.2 * attempt))
        assert last_err is not None
        elapsed_ms = int((time.perf_counter() - call_start) * 1000)
        llm_client_logger.error(
            "llm_call_failed call_id=%s model=%s attempts=%s elapsed_ms=%s message_count=%s tool_count=%s error=%s",
            call_id,
            self.model,
            attempts,
            elapsed_ms,
            message_count,
            tool_count,
            str(last_err),
        )
        if self._is_timeout_error(last_err):
            threshold_ms = int(self.THINKING_TIMEOUT_GUARDRAIL_SECONDS * 1000)
            if elapsed_ms >= threshold_ms:
                self._last_long_timeout_elapsed_ms = elapsed_ms
        raise last_err

    @staticmethod
    def _is_timeout_error(err: Exception) -> bool:
        if isinstance(err, TimeoutError):
            return True
        if isinstance(err, httpx.TimeoutException):
            return True
        text = str(err or "").lower()
        return "timeout" in text or "timed out" in text

    def _build_timeout_guardrail_message(self, *, elapsed_ms: int, call_id: str) -> ChatCompletionMessage:
        self._long_timeout_strikes += 1
        if self._long_timeout_strikes >= 2:
            content = "<TRADE_DONE>"
            llm_client_logger.error(
                "llm_timeout_guardrail_force_done call_id=%s model=%s strikes=%s elapsed_ms=%s",
                call_id,
                self.model,
                self._long_timeout_strikes,
                elapsed_ms,
            )
        else:
            content = "warning: thinking timeout"
            llm_client_logger.warning(
                "llm_timeout_guardrail_warning call_id=%s model=%s strikes=%s elapsed_ms=%s",
                call_id,
                self.model,
                self._long_timeout_strikes,
                elapsed_ms,
            )
        return ChatCompletionMessage(role="assistant", content=content, tool_calls=None)

    @staticmethod
    def _has_any_thought_sig(d: dict[str, Any]) -> bool:
        return any(d.get(k) not in (None, "") for k in _THOUGHT_SIG_KEYS)

    @staticmethod
    def _first_thought_sig(d: dict[str, Any]) -> tuple[Optional[str], Any]:
        for k in _THOUGHT_SIG_KEYS:
            v = d.get(k)
            if v is not None and v != "":
                return k, v
        return None, None

    @staticmethod
    def _sync_thought_sig_snake_camel(d: dict[str, Any]) -> None:
        """部分 Gemini 兼容网关只认 thoughtSignature，OpenAI 侧常用 snake_case；双写避免 400。"""
        if not LLMClient._has_any_thought_sig(d):
            return
        _, v = LLMClient._first_thought_sig(d)
        for k in _THOUGHT_SIG_KEYS:
            if d.get(k) in (None, ""):
                d[k] = v

    @staticmethod
    def _ensure_gemini_tool_calls_have_nonempty_thought_sig(
        tool_calls: list[dict[str, Any]],
        *,
        model: str | None,
    ) -> list[dict[str, Any]]:
        """
        单条 tool_call 时 _fill_parallel_thought_signatures 不会运行；网关仍要求 function 上存在非空 thought_signature。
        """
        if not LLMClient.is_gemini_model_name(model) or not tool_calls:
            return tool_calls
        ph = LLMClient._gemini_thought_signature_placeholder_value()
        out: list[dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                out.append(tc)
                continue
            m = dict(tc)
            fn = dict(m["function"]) if isinstance(m.get("function"), dict) else {}
            if not LLMClient._has_any_thought_sig(fn):
                ik, iv = LLMClient._first_thought_sig(m)
                fn["thought_signature"] = iv if ik is not None else ph
            if not LLMClient._has_any_thought_sig(m):
                ok, ov = LLMClient._first_thought_sig(fn)
                m["thought_signature"] = ov if ok is not None else ph
            LLMClient._sync_thought_sig_snake_camel(fn)
            LLMClient._sync_thought_sig_snake_camel(m)
            m["function"] = fn
            out.append(m)
        return out

    @staticmethod
    def _fill_parallel_thought_signatures(
        tool_calls: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        同一 assistant 消息内多条并行 tool_call 时，用首个已出现的签名补全缺失项（不覆盖已有签名）。
        键名与模板一致（snake 或 camel），便于网关识别。

        部分 Gemini 兼容网关只在 tool_call 顶层带 thought_signature，function 内无签名；
        上游仍要求每个 functionCall part 含 thought_signature，故在仅有 outer 或仅有 inner
        模板时，将二者互为回退复制。若并行调用完全无签名且 model 为 gemini，写入空字符串占位。
        """
        if len(tool_calls) < 2:
            return tool_calls
        tpl_outer_k: Optional[str] = None
        tpl_outer_v: Any = None
        tpl_inner_k: Optional[str] = None
        tpl_inner_v: Any = None
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            ok, ov = LLMClient._first_thought_sig(tc)
            if tpl_outer_k is None and ok is not None:
                tpl_outer_k, tpl_outer_v = ok, ov
            fn = tc.get("function")
            if isinstance(fn, dict):
                ik, iv = LLMClient._first_thought_sig(fn)
                if tpl_inner_k is None and ik is not None:
                    tpl_inner_k, tpl_inner_v = ik, iv
            if tpl_outer_k is not None and tpl_inner_k is not None:
                break

        if tpl_inner_k is None and tpl_outer_k is not None:
            tpl_inner_k, tpl_inner_v = tpl_outer_k, tpl_outer_v
        elif tpl_outer_k is None and tpl_inner_k is not None:
            tpl_outer_k, tpl_outer_v = tpl_inner_k, tpl_inner_v

        if tpl_outer_k is None and tpl_inner_k is None:
            if LLMClient.is_gemini_model_name(model):
                tpl_outer_k = tpl_inner_k = "thought_signature"
                tpl_outer_v = tpl_inner_v = LLMClient._gemini_thought_signature_placeholder_value()
            else:
                return tool_calls

        out: list[dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                out.append(tc)
                continue
            tc = dict(tc)
            fn = dict(tc["function"]) if isinstance(tc.get("function"), dict) else {}
            if tpl_outer_k is not None and not LLMClient._has_any_thought_sig(tc):
                tc[tpl_outer_k] = tpl_outer_v
            if tpl_inner_k is not None and not LLMClient._has_any_thought_sig(fn):
                fn[tpl_inner_k] = tpl_inner_v
            tc["function"] = fn
            out.append(tc)
        return out

    @staticmethod
    def _normalize_messages_for_api(
        messages: Sequence[Any],
        *,
        model: str | None = None,
    ) -> list[Any]:
        """
        ReAct 历史中 assistant.tool_calls 均为 dict；若后续某处用错误的 roundtrip 处理过 dict，
        或需统一形状，在此对每个 tool_call 做一次安全展开（dict / SDK 对象均可）。
        """
        out: list[Any] = []
        for m in messages:
            if not isinstance(m, dict):
                out.append(m)
                continue
            if m.get("role") == "assistant" and m.get("tool_calls"):
                mm = dict(m)
                tcs = [LLMClient._tool_call_dict_roundtrip(tc) for tc in m["tool_calls"]]
                mm["tool_calls"] = LLMClient._fill_parallel_thought_signatures(tcs, model=model)
                mm["tool_calls"] = LLMClient._ensure_gemini_tool_calls_have_nonempty_thought_sig(
                    mm["tool_calls"], model=model
                )
                out.append(mm)
            else:
                out.append(m)
        return out

    @staticmethod
    def _merge_wire_tool_call_dicts(
        tcs: list[dict[str, Any]],
        raw_list: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """将 HTTP 原始 JSON 中 tool_calls 的扩展字段合并进 roundtrip dict（不覆盖 SDK 已给出的非空值）。"""
        if not raw_list or len(raw_list) != len(tcs):
            return tcs
        out: list[dict[str, Any]] = []
        for i, tc in enumerate(tcs):
            raw = raw_list[i]
            m = dict(tc)
            for k, v in raw.items():
                if k in ("id", "type", "function"):
                    continue
                if m.get(k) not in (None, ""):
                    continue
                m[k] = v
            raw_fn = raw.get("function")
            fn = dict(m.get("function") or {}) if isinstance(m.get("function"), dict) else {}
            if isinstance(raw_fn, dict):
                for k, v in raw_fn.items():
                    if k in ("name", "arguments"):
                        continue
                    if fn.get(k) not in (None, ""):
                        continue
                    fn[k] = v
            m["function"] = fn
            out.append(m)
        return out

    def build_assistant_message_dict(self, resp) -> dict:
        """与最近一次 call() 配对的 assistant 消息 dict；Gemini 下合并 wire 层 tool_calls 扩展字段。"""
        wire = self._last_wire_tool_calls
        self._last_wire_tool_calls = None
        return LLMClient.build_message_dict(
            resp,
            model=self.model,
            wire_raw_tool_calls=wire,
        )

    @staticmethod
    def _pydantic_extra_dict(obj: Any) -> dict[str, Any]:
        """OpenAI SDK BaseModel 上供应商自定义字段（如 Gemini 的 thought_signature）。"""
        merged: dict[str, Any] = {}
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            merged.update(extra)
        pe = getattr(obj, "__pydantic_extra__", None)
        if isinstance(pe, dict):
            merged.update(pe)
        return merged

    @staticmethod
    def _tool_call_dict_roundtrip(tc: Any) -> dict[str, Any]:
        """
        将 assistant tool_call 原样序列化回请求体（含 function 内扩展字段，不做解码/改写）。
        此前手写 id/type/function 会丢掉 thought_signature，导致上游 400。
        对 **dict** 必须用键访问：getattr(dict, 'function') 为 None，会把 id/function 清空。
        """
        if isinstance(tc, dict):
            fn_src = tc.get("function")
            fn_dict: dict[str, Any] = dict(fn_src) if isinstance(fn_src, dict) else {}
            out: dict[str, Any] = {}
            for key, val in tc.items():
                if key == "function":
                    continue
                out[key] = val
            out["function"] = fn_dict
            out.setdefault("id", tc.get("id", ""))
            out.setdefault("type", tc.get("type", "function"))
            return out

        fn_obj = getattr(tc, "function", None)
        fn_dict = {}
        if fn_obj is not None:
            if hasattr(fn_obj, "model_dump"):
                fn_dict = dict(fn_obj.model_dump(mode="json", exclude_none=False))
            else:
                fn_dict = {
                    "name": getattr(fn_obj, "name", ""),
                    "arguments": getattr(fn_obj, "arguments", "{}"),
                }
            for k, v in LLMClient._pydantic_extra_dict(fn_obj).items():
                fn_dict.setdefault(k, v)
            fn_dict.setdefault("name", getattr(fn_obj, "name", ""))
            fn_dict.setdefault("arguments", getattr(fn_obj, "arguments", "{}"))

        if hasattr(tc, "model_dump"):
            tc_dict = dict(tc.model_dump(mode="json", exclude_none=False))
        else:
            tc_dict = {
                "id": getattr(tc, "id", ""),
                "type": getattr(tc, "type", "function"),
                "function": fn_dict,
            }
        for k, v in LLMClient._pydantic_extra_dict(tc).items():
            if k not in ("id", "type", "function"):
                tc_dict.setdefault(k, v)
        tc_dict["id"] = getattr(tc, "id", tc_dict.get("id", ""))
        tc_dict["type"] = getattr(tc, "type", tc_dict.get("type", "function"))
        tc_dict["function"] = fn_dict
        return tc_dict

    @staticmethod
    def tool_calls_to_roundtrip_dicts(tool_calls: Optional[List[Any]]) -> Optional[List[dict[str, Any]]]:
        if not tool_calls:
            return None
        return [LLMClient._tool_call_dict_roundtrip(tc) for tc in tool_calls]

    @staticmethod
    def tool_call_parts(tc: Any) -> tuple[str, str, str]:
        """Return (tool_call_id, function_name, function_arguments_json_text)."""
        if isinstance(tc, dict):
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            tc_id = str(tc.get("id") or "")
            return tc_id, str(fn.get("name") or ""), str(fn.get("arguments") or "{}")
        fn_obj = getattr(tc, "function", None)
        tc_id = str(getattr(tc, "id", "") or "")
        fn_name = str(getattr(fn_obj, "name", "") or "")
        fn_args = str(getattr(fn_obj, "arguments", "{}") or "{}")
        return tc_id, fn_name, fn_args

    @staticmethod
    def _canonical_tool_call_arguments(arguments_text: str) -> str:
        text = str(arguments_text or "{}")
        try:
            parsed = json.loads(text)
        except Exception:
            return text.strip()
        try:
            return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except Exception:
            return text.strip()

    @staticmethod
    def _tool_call_dedupe_signature(tc: dict[str, Any]) -> str:
        _, name, args_text = LLMClient.tool_call_parts(tc)
        # tc_id is intentionally excluded from signature so duplicated invocations with different ids are filtered.
        return f"{name}|{LLMClient._canonical_tool_call_arguments(args_text)}"

    @staticmethod
    def apply_tool_call_guardrails(
        tool_calls: Optional[List[Any]],
        *,
        model: str | None = None,
        max_calls: int | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Apply per-assistant-turn tool-call guardrails:
        1) hard cap count
        2) remove repeated identical (name+arguments) calls in the same turn.
        """
        if not tool_calls:
            return [], []

        cap = int(max_calls or LLMClient.MAX_TOOL_CALLS_PER_ASSISTANT_TURN)
        cap = max(1, cap)
        raw = [LLMClient._tool_call_dict_roundtrip(tc) for tc in tool_calls]
        raw_count = len(raw)
        warnings: list[str] = []

        capped = raw[:cap]
        truncated_count = max(0, raw_count - len(capped))
        if truncated_count > 0:
            warnings.append(
                f"Tool-call limit exceeded: requested {raw_count}, capped at {cap}, dropped {truncated_count}."
            )

        deduped: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()
        duplicate_count = 0
        for tc in capped:
            sig = LLMClient._tool_call_dedupe_signature(tc)
            if sig in seen_signatures:
                duplicate_count += 1
                continue
            seen_signatures.add(sig)
            deduped.append(tc)

        if duplicate_count > 0:
            warnings.append(
                f"Repeated identical tool calls detected in one response; filtered {duplicate_count} duplicate calls."
            )

        if warnings:
            llm_client_logger.warning(
                "tool_call_guardrail_applied model=%s requested=%s capped=%s deduped=%s warnings=%s",
                model or "",
                raw_count,
                len(capped),
                len(deduped),
                " | ".join(warnings),
            )

        return deduped, warnings

    @staticmethod
    def tool_guardrail_warning_user_message(warnings: Sequence[str]) -> dict[str, str]:
        warn_text = " | ".join([str(w).strip() for w in warnings if str(w).strip()])
        return {
            "role": "user",
            "content": (
                "Guardrail warning: Your previous response had excessive or repeated tool calls. "
                f"{warn_text} "
                "Only the retained tool calls were executed and added to context. "
                "Do not repeat identical tool calls in one response."
            ),
        }

    @staticmethod
    def build_message_dict(
        resp,
        *,
        model: str | None = None,
        wire_raw_tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict:
        """
        将 ChatCompletionMessage 安全序列化为可写入历史的 dict。
        完整保留 tool_calls / function 上的供应商扩展字段（如 thought_signature），按原样回传。
        wire_raw_tool_calls: 来自 HTTP 原始 JSON 的 tool_calls 列表（与 resp.tool_calls 按序对齐）。
        """
        tool_calls = getattr(resp, "tool_calls", None)
        if hasattr(resp, "model_dump"):
            msg = dict(resp.model_dump(mode="json", exclude_none=False))
        else:
            msg = dict(resp)

        for k, v in LLMClient._pydantic_extra_dict(resp).items():
            msg.setdefault(k, v)

        if tool_calls:
            tcs = [LLMClient._tool_call_dict_roundtrip(tc) for tc in tool_calls]
            tcs = LLMClient._merge_wire_tool_call_dicts(tcs, wire_raw_tool_calls)
            msg["tool_calls"] = LLMClient._fill_parallel_thought_signatures(tcs, model=model)
            msg["tool_calls"] = LLMClient._ensure_gemini_tool_calls_have_nonempty_thought_sig(
                msg["tool_calls"], model=model
            )

        return msg

    @staticmethod
    def extract_text_content(message: Any) -> str:
        """兼容不同供应商响应格式，尽量提取可展示的文本。"""
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

    @staticmethod
    def gemini_post_tool_user_message() -> dict[str, str]:
        """
        部分 Gemini OpenAI 兼容网关要求：若 messages 以 tool 结尾直接发起下一轮 completion 会误报
        thought_signature 缺失；在 tool 后插入一条 user 可消除该 400。
        """
        return {
            "role": "user",
            "content": (
                "Tool results are attached above. Continue: invoke more tools if needed, "
                "or complete the task per system instructions."
            ),
        }

    def test_connection(self, timeout_seconds: Optional[float] = 15.0) -> str:
        """
        使用与运行时一致的 chat.completions 测试连通性。
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
