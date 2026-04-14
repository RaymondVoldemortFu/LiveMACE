import json
import logging
import re
import time
from html import unescape
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests

from config.tool_config import ToolConfig
from services.agent.prompts.sub_agent_prompts import SUB_AGENT_SYSTEM_PROMPT
from services.agent.llm_client import LLMClient

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")
agent_logger = logging.getLogger("agent_decision")
search_logger = logging.getLogger("search_results")

# Try to import tiktoken for accurate token counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken not available, using approximate token counting")


class SearchSubAgent:
    def __init__(self, model: str = "gpt-4o-mini", api_key: str = None, base_url: str = None, agent_name: Optional[str] = None):
        self.brightdata_api_key = ToolConfig.brightdata_api_key
        self._brightdata_client_cls = None
        if self.brightdata_api_key:
            try:
                from brightdata import SyncBrightDataClient
                self._brightdata_client_cls = SyncBrightDataClient
            except ImportError:
                logger.error("Bright Data SDK not installed. Please run `uv add brightdata-sdk`.")
        else:
            logger.warning("BRIGHTDATA_API_KEY not found in environment variables.")

        # LLM client for sub-agent reasoning
        self.llm_client = LLMClient(model=model,
            api_key=api_key,
            base_url=base_url,
        ) if api_key else None
        self.model = model
        self.max_steps = ToolConfig.MAX_SEARCH_STEPS
        self.max_context_tokens = ToolConfig.MAX_CONTEXT_TOKENS
        self.agent_name = agent_name or "SearchSubAgent"
        self.max_retries = ToolConfig.SEARCH_AGENT_MAX_RETRIES
        # Hotfix: cap tool executions per single LLM response to prevent search storms.
        self.max_tools_per_step = LLMClient.MAX_TOOL_CALLS_PER_ASSISTANT_TURN
        self.search_timeout_seconds = ToolConfig.SEARCH_AGENT_SEARCH_TIMEOUT_SECONDS
        self.unlocker_timeout_seconds = ToolConfig.SEARCH_AGENT_UNLOCKER_TIMEOUT_SECONDS
        self.local_fetch_connect_timeout_seconds = (
            ToolConfig.SEARCH_AGENT_LOCAL_FETCH_CONNECT_TIMEOUT_SECONDS
        )
        self.local_fetch_read_timeout_seconds = ToolConfig.SEARCH_AGENT_LOCAL_FETCH_READ_TIMEOUT_SECONDS
        
        # Initialize tokenizer for accurate counting if available
        self.tokenizer = None
        if TIKTOKEN_AVAILABLE:
            try:
                self.tokenizer = tiktoken.encoding_for_model(model)
            except KeyError:
                # Fallback to cl100k_base for unknown models
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

    @staticmethod
    def _google_recency_tbs(time_range: Optional[str]) -> Optional[str]:
        """Map UI enum to Google `tbs=qdr:` segment (Bright Data SERP URL builder)."""
        if not time_range or str(time_range).lower() == "none":
            return None
        return {"day": "d", "week": "w", "month": "m", "year": "y"}.get(str(time_range).lower())

    def _search_tool(self, query: str, topic: str = "general", time_range: str = None,
                     max_results: int = 5) -> Dict[str, Any]:
        """
        Executes a search query using Bright Data Google SERP (`query`, `num_results`, optional `time_range` tbs).
        """
        if not self._brightdata_client_cls:
            return {"error": "Bright Data client not initialized."}

        try:
            merged_query = self._merge_topic_into_query(query=query, topic=topic)
            serp_kwargs: Dict[str, Any] = {"num_results": max_results}
            tbs = self._google_recency_tbs(time_range)
            if tbs:
                serp_kwargs["time_range"] = tbs
            serp_results = self._call_with_retry(
                operation_name="search_tool",
                timeout_seconds=self.search_timeout_seconds,
                func=lambda: self._search_once(merged_query, serp_kwargs),
            )
            return self._normalize_serp_results(
                query=query,
                merged_query=merged_query,
                topic=topic,
                time_range=time_range,
                payload=serp_results,
            )
        except Exception as e:
            logger.exception(f"Search failed: {e}")
            return {"error": str(e)}

    def _extract_tool(self, url: str) -> Dict[str, Any]:
        """
        Extracts content from a URL.
        Optimization: use local fetch first, fallback to Bright Data Unlocker on failure.
        """
        local_result = self._extract_with_local_fetch(url)
        if not local_result.get("error"):
            return local_result

        if not self._brightdata_client_cls:
            return {
                "error": "Bright Data client not initialized.",
                "local_attempt": local_result
            }
        try:
            unlocker_result = self._call_with_retry(
                operation_name="unlocker_extract",
                timeout_seconds=self.unlocker_timeout_seconds,
                func=lambda: self._unlocker_once(url),
            )
            normalized = self._normalize_unlocker_result(url=url, payload=unlocker_result)
            if normalized.get("error"):
                logger.warning(f"Unlocker extraction returned error for {url}: {normalized.get('error')}")
                return {
                    "error": normalized.get("error"),
                    "local_attempt": local_result,
                    "unlocker_attempt": normalized
                }
            normalized["fallback"] = "unlocker"
            normalized["local_attempt"] = local_result
            return normalized
        except Exception as e:
            if "unlocker_extract failed" in str(e):
                logger.warning(f"Extract warning: {e}")
            else:
                logger.exception(f"Extract failed: {e}")
            return {"error": str(e), "local_attempt": local_result}

    def _merge_topic_into_query(self, query: str, topic: str = "general") -> str:
        """Optional topic hint merged into the query string (not a SERP API field)."""
        segments = [query.strip()]
        if topic and topic.lower() in {"news", "finance"}:
            segments.append(topic.lower())
        return " ".join([s for s in segments if s]).strip()

    def _normalize_serp_results(
        self,
        query: str,
        merged_query: str,
        topic: str,
        time_range: Optional[str],
        payload: Any,
    ) -> Dict[str, Any]:
        """
        Normalize Bright Data SERP response into internal result schema.
        """
        if getattr(payload, "success", True) is False and getattr(payload, "error", None):
            return {
                "error": str(payload.error),
                "query": query,
                "effective_query": merged_query,
                "topic": topic,
                "time_range": time_range,
                "results": [],
            }

        data = getattr(payload, "data", payload)
        if isinstance(data, dict):
            items = data.get("results") or data.get("data") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []

        normalized_results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("link") or item.get("url") or ""
            title = item.get("title") or "Untitled Source"
            snippet = item.get("description") or item.get("snippet") or item.get("content") or ""
            normalized_results.append(
                {
                    "title": str(title),
                    "url": str(url),
                    "content": str(snippet),
                    "raw_content": str(snippet),
                }
            )

        return {
            "query": query,
            "effective_query": merged_query,
            "topic": topic,
            "time_range": time_range,
            "results": normalized_results,
        }

    def _extract_with_local_fetch(self, url: str) -> Dict[str, Any]:
        """
        Basic local extraction as fast path before using Unlocker.
        """
        try:
            response = self._call_with_retry(
                operation_name="local_fetch",
                timeout_seconds=self.local_fetch_connect_timeout_seconds
                + self.local_fetch_read_timeout_seconds + 1,
                func=lambda: requests.get(
                    url,
                    timeout=(
                        self.local_fetch_connect_timeout_seconds,
                        self.local_fetch_read_timeout_seconds,
                    ),
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        )
                    },
                ),
            )
            response.raise_for_status()
            html = response.text or ""
            text = self._clean_html_to_text(html)
            if len(text) < 100:
                return {"error": "Local fetch succeeded but extracted text is too short."}
            title = self._extract_title(html)
            return {
                "results": [
                    {
                        "url": url,
                        "title": title or "Untitled Source",
                        "content": text[:8000],
                        "raw_content": text[:12000],
                    }
                ],
                "fallback": "local",
            }
        except Exception as e:
            return {"error": f"Local fetch failed: {e}"}

    def _search_once(self, merged_query: str, serp_kwargs: Dict[str, Any]) -> Any:
        with self._brightdata_client_cls(token=self.brightdata_api_key) as client:
            return client.search.google(query=merged_query, **serp_kwargs)

    def _unlocker_once(self, url: str) -> Any:
        with self._brightdata_client_cls(token=self.brightdata_api_key) as client:
            return client.scrape_url(url=url)

    def _call_with_retry(self, operation_name: str, timeout_seconds: float, func):
        last_err: Optional[Exception] = None
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(func)
                return future.result(timeout=timeout_seconds)
            except FuturesTimeoutError as err:
                last_err = TimeoutError(
                    f"{operation_name} timeout after {timeout_seconds}s (attempt {attempt}/{total_attempts})"
                )
                logger.warning(str(last_err))
            except Exception as err:
                last_err = err
                logger.warning(
                    "%s failed (attempt %s/%s): %s",
                    operation_name,
                    attempt,
                    total_attempts,
                    err,
                )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if attempt < total_attempts:
                time.sleep(min(1.0, 0.2 * attempt))
        raise RuntimeError(
            f"{operation_name} failed after {total_attempts} attempts: {last_err}"
        )

    @staticmethod
    def _is_unlocker_error_result(result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict):
            return False
        unlocker_attempt = result.get("unlocker_attempt")
        if isinstance(unlocker_attempt, dict) and unlocker_attempt.get("error"):
            return True
        message = str(result.get("error", "")).lower()
        return "unlocker" in message

    def _normalize_unlocker_result(self, url: str, payload: Any) -> Dict[str, Any]:
        """
        Normalize Unlocker response into current extraction schema.
        """
        data = getattr(payload, "data", payload)
        content = ""
        title = "Untitled Source"

        if isinstance(data, dict):
            content = (
                data.get("content")
                or data.get("text")
                or data.get("body")
                or data.get("html")
                or ""
            )
            title = data.get("title") or title
        elif isinstance(data, str):
            content = data

        if not content:
            return {"error": "Unlocker returned empty content."}

        if "<html" in content.lower():
            cleaned = self._clean_html_to_text(content)
            title = self._extract_title(content) or title
        else:
            cleaned = str(content).strip()

        if len(cleaned) < 100:
            return {"error": "Unlocker content is too short."}

        return {
            "results": [
                {
                    "url": url,
                    "title": title,
                    "content": cleaned[:8000],
                    "raw_content": cleaned[:12000],
                }
            ]
        }

    @staticmethod
    def _extract_title(html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return unescape(re.sub(r"\s+", " ", match.group(1))).strip()

    @staticmethod
    def _clean_html_to_text(html: str) -> str:
        without_script = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
        without_style = re.sub(r"<style[\s\S]*?</style>", " ", without_script, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", without_style)
        text = unescape(text)
        return re.sub(r"\s+", " ", text).strip()
    
    def _estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        Estimate the total number of tokens in the message list.
        Uses tiktoken if available, otherwise approximates with char count / 4.
        """
        if self.tokenizer:
            # Accurate counting with tiktoken
            total = 0
            for msg in messages:
                # Count role
                total += len(self.tokenizer.encode(msg.get("role", "")))
                # Count content
                if "content" in msg and msg["content"]:
                    total += len(self.tokenizer.encode(str(msg["content"])))
                # Count tool calls if present
                if "tool_calls" in msg and msg["tool_calls"]:
                    total += len(self.tokenizer.encode(json.dumps(msg["tool_calls"])))
                # Count function name for tool messages
                if "name" in msg:
                    total += len(self.tokenizer.encode(msg["name"]))
            return total
        else:
            # Approximate: 1 token ≈ 4 characters for English text
            total_chars = len(json.dumps(messages, ensure_ascii=False))
            return total_chars // 4
    
    def _summarize_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Summarize the conversation history to reduce token count.
        Keeps system prompt and recent context, summarizes the middle.
        """
        if len(messages) <= 3:  # system + user + 1 response
            return messages
        
        agent_logger.warning(f"Context approaching limit, summarizing {len(messages)} messages")
        
        # Keep system prompt (first message) and last 2 messages
        system_msg = messages[0]
        recent_msgs = messages[-2:]
        middle_msgs = messages[1:-2]
        
        # Create summary of middle messages
        summary_content = "Previous conversation summary:\n"
        for msg in middle_msgs:
            role = msg.get("role", "unknown")
            if role == "assistant":
                content = msg.get("content", "")
                if content:
                    summary_content += f"- Assistant: {content[:200]}...\n"
                if msg.get("tool_calls"):
                    summary_content += f"- Assistant called {len(msg['tool_calls'])} tool(s)\n"
            elif role == "tool":
                tool_name = msg.get("name", "unknown")
                summary_content += f"- Executed {tool_name}\n"
        
        # Call LLM to create a concise summary
        try:
            summary_request = [
                {"role": "system", "content": "You are a summarization assistant. Summarize the following search conversation history concisely, preserving key information and search results."},
                {"role": "user", "content": summary_content}
            ]

            msg = self.llm_client.call(summary_request)
            summarized_text = LLMClient.extract_text_content(msg)
            agent_logger.info(f"Summarized {len(middle_msgs)} messages into summary")
            
            # Return: system + summary + recent messages
            return [
                system_msg,
                {"role": "assistant", "content": f"[Context Summary]: {summarized_text}"},
                *recent_msgs
            ]
        except Exception as e:
            logger.error(f"Failed to summarize messages: {e}")
            agent_logger.error(f"Summarization failed, truncating history instead")
            # Fallback: just keep system + last 3 messages
            return [system_msg] + messages[-3:]

    def _collect_sources_from_messages(self, messages: List[Dict[str, Any]], max_sources: int = 5) -> List[Dict[str, str]]:
        """
        Collect source candidates from tool outputs already present in message history.
        This is used as a safe fallback when the model fails to emit FINAL_RESPONSE in time.
        """
        sources: List[Dict[str, str]] = []
        seen_urls = set()

        for msg in reversed(messages):
            if msg.get("role") != "tool":
                continue

            raw = msg.get("content")
            if not raw:
                continue

            try:
                payload = json.loads(raw)
            except Exception:
                continue

            results = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(results, list):
                continue

            for item in results:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if not url or url in seen_urls:
                    continue

                seen_urls.add(url)
                title = item.get("title") or "Untitled Source"
                snippet = item.get("content") or item.get("raw_content") or ""
                snippet = (str(snippet)[:500] + "...") if len(str(snippet)) > 500 else str(snippet)
                sources.append({"title": str(title), "url": str(url), "snippet": snippet})

                if len(sources) >= max_sources:
                    return sources

        return sources

    def _forced_final_response(self, messages: List[Dict[str, Any]], latest_content: str = "") -> Dict[str, Any]:
        """
        Build a non-error structured response when step budget is exhausted.
        """
        sources = self._collect_sources_from_messages(messages, max_sources=5)
        summary = self._generate_forced_summary(messages, sources, latest_content)
        if not summary:
            if sources:
                summary = (
                    "Search completed but reached max internal steps before final formatting. "
                    "Returning the key sources collected so far."
                )
            else:
                summary = (
                    "Search reached max internal steps before producing a final formatted answer, "
                    "and no reliable sources were retained."
                )

        return {"summary": summary, "sources": sources}

    def _extract_query_context(self, messages: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Recover the original query context from the initial user message in run().
        """
        context = {"query": "", "topic": "", "time_range": ""}
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content") or "")
            if "Query:" not in content:
                continue
            for line in content.splitlines():
                if line.startswith("Query:"):
                    context["query"] = line.replace("Query:", "", 1).strip()
                elif line.startswith("Topic:"):
                    context["topic"] = line.replace("Topic:", "", 1).strip()
                elif line.startswith("Time Range:"):
                    context["time_range"] = line.replace("Time Range:", "", 1).strip()
            break
        return context

    def _generate_forced_summary(
        self,
        messages: List[Dict[str, Any]],
        sources: List[Dict[str, str]],
        latest_content: str = "",
    ) -> str:
        """
        Force one final summarization pass without tools when step budget is exhausted.
        """
        if not self.llm_client:
            return latest_content.strip()

        context = self._extract_query_context(messages)
        condensed_sources = []
        for s in sources[:5]:
            condensed_sources.append({
                "title": s.get("title", ""),
                "url": s.get("url", ""),
                "snippet": (s.get("snippet", "") or "")[:300]
            })

        summarization_messages = [
            {
                "role": "system",
                "content": (
                    "You are a financial news summarization assistant. "
                    "Produce a concise, evidence-based final summary from provided sources only. "
                    "Do not call tools. Do not fabricate facts. "
                    "If evidence is weak or off-topic, state uncertainty explicitly."
                ),
            },
            {
                "role": "user",
                "content": (
                    "We reached the internal step limit in a search agent.\n"
                    "Please produce the final summary now.\n\n"
                    f"Original query: {context.get('query', '')}\n"
                    f"Topic: {context.get('topic', '')}\n"
                    f"Time range: {context.get('time_range', '')}\n"
                    f"Latest assistant draft (may be incomplete): {latest_content.strip()}\n\n"
                    f"Collected sources (JSON): {json.dumps(condensed_sources, ensure_ascii=False)}\n\n"
                    "Return plain text summary only."
                ),
            },
        ]

        try:
            msg = self.llm_client.call(summarization_messages)
            summary = LLMClient.extract_text_content(msg).strip()
            if not summary and isinstance(msg, dict):
                summary = str(msg.get("content") or "").strip()
            return summary
        except Exception as e:
            logger.warning(f"Forced summary generation failed: {e}")
            return latest_content.strip()

    def run(self, query: str, topic: str = "general", time_range: str = "none",
            max_results: int = 5) -> Dict[str, Any]:
        """
        Main entry point for the sub-agent.
        Orchestrates the search and extraction process.
        """
        if not self.llm_client or not self._brightdata_client_cls:
            return {"error": "Sub-agent not fully initialized (missing LLM or Bright Data key)."}

        agent_name = self.agent_name
        agent_logger.info(f"[{agent_name}] === Starting Search Sub-Agent ===")
        agent_logger.info(f"[{agent_name}] Query: {query}, Topic: {topic}, Time: {time_range}")

        messages = [
            {"role": "system", "content": SUB_AGENT_SYSTEM_PROMPT.format(max_steps=self.max_steps)},
            {"role": "user", "content": f"Query: {query}\nTopic: {topic}\nTime Range: {time_range}\nMax Results: {max_results}"}
        ]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_tool",
                    "description": "Google SERP search via Bright Data (query + optional recency and result count).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search keywords."},
                            "topic": {
                                "type": "string",
                                "enum": ["general", "news", "finance"],
                                "description": "Optional hint; news/finance are appended to the query text.",
                            },
                            "time_range": {
                                "type": "string",
                                "enum": ["day", "week", "month", "year", "none"],
                                "description": "Google recency filter (tbs qdr); none = no date filter.",
                            },
                            "max_results": {"type": "integer", "description": "Number of organic results to fetch."},
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "extract_tool",
                    "description": "Extract content from a specific URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"}
                        },
                        "required": ["url"]
                    }
                }
            }
        ]

        for step in range(self.max_steps):
            agent_logger.info(f"[{agent_name}] --- Sub-Agent Step {step+1}/{self.max_steps} ---")
            
            # Check token count before making request
            current_tokens = self._estimate_tokens(messages)
            agent_logger.info(f"Current context: ~{current_tokens} tokens")
            
            # If approaching limit, summarize
            if current_tokens > self.max_context_tokens * 0.8:  # 80% threshold
                agent_logger.warning(f"Context at {current_tokens}/{self.max_context_tokens} tokens, triggering summarization")
                messages = self._summarize_messages(messages)
                current_tokens = self._estimate_tokens(messages)
                agent_logger.info(f"After summarization: ~{current_tokens} tokens")
            
            try:
                # Log Request
                llm_logger.info(f"[{agent_name}] --- Sub-Agent Step {step+1} Request ---")
                llm_logger.info(json.dumps({"agent": agent_name, "payload": messages}, ensure_ascii=False, indent=2))

                msg = self.llm_client.call(messages, tools)

                # Handle message object for logging/history
                msg_dict = self.llm_client.build_assistant_message_dict(msg)
                tool_calls, tool_guard_warnings = LLMClient.apply_tool_call_guardrails(
                    getattr(msg, "tool_calls", None),
                    model=getattr(self.llm_client, "model", None),
                    max_calls=self.max_tools_per_step,
                )
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                else:
                    msg_dict.pop("tool_calls", None)
                if tool_guard_warnings:
                    agent_logger.warning(
                        f"[{agent_name}] Tool-call guardrails triggered: {' | '.join(tool_guard_warnings)}"
                    )
                messages.append(msg_dict)

                # Log Response
                llm_logger.info(f"[{agent_name}] --- Sub-Agent Step {step+1} Response ---")
                llm_logger.info(json.dumps({"agent": agent_name, "payload": msg_dict}, ensure_ascii=False, indent=2))
                content = LLMClient.extract_text_content(msg)
                agent_logger.info(f"[{agent_name}] Sub-Agent Content: {content}")

                # Check for tool calls
                tool_calls = msg_dict.get("tool_calls") or []
                if tool_calls:
                    # Last step cannot safely execute more tools; force a structured final response.
                    if step == self.max_steps - 1:
                        agent_logger.warning(
                            f"[{agent_name}] Max steps reached with pending tool calls; forcing structured final response"
                        )
                        content = LLMClient.extract_text_content(msg)
                        return self._forced_final_response(messages, latest_content=content)

                    agent_logger.info(f"[{agent_name}] Sub-Agent requested {len(tool_calls)} tools")
                    for tc in tool_calls:
                        tc_id, func_name, tc_arguments = LLMClient.tool_call_parts(tc)
                        try:
                            args = json.loads(tc_arguments or "{}")
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"Invalid sub-agent tool arguments for {func_name}: {e}; raw={tc_arguments!r}"
                            )
                            args = {}
                        agent_logger.info(f"Executing {func_name} with args: {tc_arguments}")
                        
                        result = None
                        if func_name == "search_tool":
                            t_query = args.get("query")
                            t_topic = args.get("topic", topic)
                            t_time = args.get("time_range", time_range)
                            t_max = args.get("max_results", max_results)
                            
                            # Log that we are searching, but put results in search_logger
                            logger.info(f"Sub-Agent performing search: {t_query}")
                            result = self._search_tool(t_query, t_topic, t_time, t_max)
                            if isinstance(result, dict) and result.get("error"):
                                logger.error(
                                    f"search_tool returned error for query={t_query!r}: {result.get('error')}"
                                )
                            
                            # Log full search results to dedicated logger
                            search_logger.info(f"--- Search Results for '{t_query}' ---")
                            search_logger.info(json.dumps(result, ensure_ascii=False, indent=2))
                            
                            # In agent_logger, just note success
                            agent_logger.info(f"[{agent_name}] Search completed (results logged to search_results.log)")
                            
                        elif func_name == "extract_tool":
                            url = args.get("url")
                            logger.info(f"Sub-Agent extracting: {url}")
                            result = self._extract_tool(url)
                            if isinstance(result, dict) and result.get("error"):
                                if self._is_unlocker_error_result(result):
                                    logger.warning(
                                        f"extract_tool unlocker returned warning for url={url!r}: {result.get('error')}"
                                    )
                                else:
                                    logger.error(
                                        f"extract_tool returned error for url={url!r}: {result.get('error')}"
                                    )
                            
                            # Log full extraction results to dedicated logger
                            search_logger.info(f"--- Extract Results for '{url}' ---")
                            search_logger.info(json.dumps(result, ensure_ascii=False, indent=2))
                            
                            agent_logger.info(f"[{agent_name}] Extraction completed (results logged to search_results.log)")
                        
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "name": func_name,
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                    if tool_guard_warnings:
                        messages.append(LLMClient.tool_guardrail_warning_user_message(tool_guard_warnings))
                    if self.llm_client.is_gemini_model():
                        messages.append(LLMClient.gemini_post_tool_user_message())
                else:
                    # No tool calls, check for final response in content
                    match = re.search(r"<FINAL_RESPONSE>(.*?)</FINAL_RESPONSE>", content, re.DOTALL)
                    if match:
                        try:
                            final_response = json.loads(match.group(1).strip())
                            agent_logger.info(f"[{agent_name}] Sub-Agent Final Response: {json.dumps(final_response, ensure_ascii=False)}")
                            return final_response
                        except json.JSONDecodeError:
                            logger.error("Failed to parse final JSON from sub-agent.")
                            agent_logger.error(f"[{agent_name}] Failed to parse final JSON from sub-agent.")
                            return {"summary": content, "sources": []} # Fallback
                    
                    # If no final tag but loop is ending or just chatter?
                    # If it's the last step and no final response, try to return content
                    if step == self.max_steps - 1:
                        agent_logger.warning(
                            f"[{agent_name}] Max steps reached without <FINAL_RESPONSE>; forcing structured final response"
                        )
                        forced = self._forced_final_response(messages, latest_content=content)
                        if isinstance(forced, dict) and forced:
                            return forced
                        return {"summary": content, "sources": []}

            except Exception as e:
                logger.error(f"Sub-agent step failed: {e}")
                agent_logger.error(f"[{agent_name}] Sub-agent step failed: {e}")
                return {"error": str(e)}

        agent_logger.warning("Loop exited without final response; returning forced structured fallback")
        return self._forced_final_response(messages, latest_content="")
