import json
import logging
import re
from typing import Dict, Any, List, Optional

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
        self.tavily_api_key = ToolConfig.tavily_api_key
        self.client = None
        if self.tavily_api_key:
            try:
                from tavily import TavilyClient
                self.client = TavilyClient(api_key=self.tavily_api_key)
            except ImportError:
                logger.error("Tavily SDK not installed. Please run `pip install tavily-python`.")
        else:
            logger.warning("TAVILY_API_KEY not found in environment variables.")

        # LLM client for sub-agent reasoning
        self.llm_client = LLMClient(model=model,
            api_key=api_key,
            base_url=base_url,
        ) if api_key else None
        self.model = model
        self.max_steps = ToolConfig.MAX_SEARCH_STEPS
        self.max_context_tokens = ToolConfig.MAX_CONTEXT_TOKENS
        self.agent_name = agent_name or "SearchSubAgent"
        
        # Initialize tokenizer for accurate counting if available
        self.tokenizer = None
        if TIKTOKEN_AVAILABLE:
            try:
                self.tokenizer = tiktoken.encoding_for_model(model)
            except KeyError:
                # Fallback to cl100k_base for unknown models
                self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def _search_tool(self, query: str, topic: str = "general", time_range: str = None, 
                     search_depth: str = "basic", max_results: int = 5) -> Dict[str, Any]:
        """
        Executes a search query using Tavily API.
        """
        if not self.client:
            return {"error": "Tavily client not initialized."}

        try:
            # Include answer is set to None (per user requirement to hardcode include_answer=None? 
            # User said "Include answer parameter hardcoded to none".
            # In Tavily python SDK, include_answer expects bool or "basic"/"advanced". 
            # "None" might mean exclude it. Let's set to False to be safe if "none" isn't valid param value but logic intent.)
            # Re-reading: "Include answer parameter hardcoded to none" -> likely means don't ask Tavily for AI answer, let sub-agent do it.
            
            response = self.client.search(
                query=query,
                topic=topic,
                time_range=time_range if time_range != "none" else None,
                search_depth=search_depth,
                max_results=max_results,
                include_answer=False, 
                include_raw_content=False # We use extract for detailed content
            )
            return response
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {"error": str(e)}

    def _extract_tool(self, url: str) -> Dict[str, Any]:
        """
        Extracts content from a URL using Tavily API.
        """
        if not self.client:
            return {"error": "Tavily client not initialized."}

        try:
            # extract returns a dict with 'results' list
            response = self.client.extract(urls=url)
            # Simple summary/cleaning could be done here if needed, but raw return is fine for LLM
            return response
        except Exception as e:
            logger.error(f"Extract failed: {e}")
            return {"error": str(e)}
    
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
            search_depth: str = "basic", max_results: int = 5) -> Dict[str, Any]:
        """
        Main entry point for the sub-agent.
        Orchestrates the search and extraction process.
        """
        if not self.llm_client or not self.client:
            return {"error": "Sub-agent not fully initialized (missing LLM or Tavily key)."}

        agent_name = self.agent_name
        agent_logger.info(f"[{agent_name}] === Starting Search Sub-Agent ===")
        agent_logger.info(f"[{agent_name}] Query: {query}, Topic: {topic}, Time: {time_range}")

        messages = [
            {"role": "system", "content": SUB_AGENT_SYSTEM_PROMPT.format(max_steps=self.max_steps)},
            {"role": "user", "content": f"Query: {query}\nTopic: {topic}\nTime Range: {time_range}\nDepth: {search_depth}\nMax Results: {max_results}"}
        ]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_tool",
                    "description": "Execute a web search.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "topic": {"type": "string", "enum": ["general", "news", "finance"]},
                            "time_range": {"type": "string", "enum": ["day", "week", "month", "year", "none"]},
                            "search_depth": {"type": "string", "enum": ["basic", "advanced"]},
                            "max_results": {"type": "integer"}
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
                msg_dict = msg.model_dump() if hasattr(msg, 'model_dump') else msg
                messages.append(msg_dict)

                # Log Response
                llm_logger.info(f"[{agent_name}] --- Sub-Agent Step {step+1} Response ---")
                llm_logger.info(json.dumps({"agent": agent_name, "payload": msg_dict}, ensure_ascii=False, indent=2))
                content = LLMClient.extract_text_content(msg)
                agent_logger.info(f"[{agent_name}] Sub-Agent Content: {content}")

                # Check for tool calls
                tool_calls = msg.tool_calls if hasattr(msg, 'tool_calls') else (msg_dict.get('tool_calls') or [])
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
                        # Support both object and dict formats
                        if isinstance(tc, dict):
                            func_name = tc.get('function', {}).get('name')
                            tc_id = tc.get('id')
                            tc_arguments = tc.get('function', {}).get('arguments', '{}')
                        else:
                            func_name = tc.function.name
                            tc_id = tc.id
                            tc_arguments = tc.function.arguments
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
                            t_depth = args.get("search_depth", search_depth)
                            t_max = args.get("max_results", max_results)
                            
                            # Log that we are searching, but put results in search_logger
                            logger.info(f"Sub-Agent performing search: {t_query}")
                            result = self._search_tool(t_query, t_topic, t_time, t_depth, t_max)
                            
                            # Log full search results to dedicated logger
                            search_logger.info(f"--- Search Results for '{t_query}' ---")
                            search_logger.info(json.dumps(result, ensure_ascii=False, indent=2))
                            
                            # In agent_logger, just note success
                            agent_logger.info(f"[{agent_name}] Search completed (results logged to search_results.log)")
                            
                        elif func_name == "extract_tool":
                            url = args.get("url")
                            logger.info(f"Sub-Agent extracting: {url}")
                            result = self._extract_tool(url)
                            
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
