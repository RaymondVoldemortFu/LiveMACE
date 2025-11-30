import json
import logging
import re
from typing import Dict, Any, List, Optional

from openai import OpenAI

from config.tool_config import ToolConfig
from services.agent.prompts.sub_agent_prompts import SUB_AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("llm_trace")
agent_logger = logging.getLogger("agent_decision")
search_logger = logging.getLogger("search_results")


class SearchSubAgent:
    def __init__(self, model: str = "gpt-4o-mini", api_key: str = None, base_url: str = None):
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
        self.llm_client = OpenAI(api_key=api_key, base_url=base_url) if api_key else None
        self.model = model
        self.max_steps = ToolConfig.MAX_SEARCH_STEPS

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

    def run(self, query: str, topic: str = "general", time_range: str = "none", 
            search_depth: str = "basic", max_results: int = 5) -> Dict[str, Any]:
        """
        Main entry point for the sub-agent.
        Orchestrates the search and extraction process.
        """
        if not self.llm_client or not self.client:
            return {"error": "Sub-agent not fully initialized (missing LLM or Tavily key)."}

        agent_logger.info(f"=== Starting Search Sub-Agent ===")
        agent_logger.info(f"Query: {query}, Topic: {topic}, Time: {time_range}")

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
            agent_logger.info(f"--- Sub-Agent Step {step+1}/{self.max_steps} ---")
            
            try:
                # Log Request
                llm_logger.info(f"--- Sub-Agent Step {step+1} Request ---")
                llm_logger.info(json.dumps(messages, ensure_ascii=False, indent=2))

                response = self.llm_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=0.4
                )
                
                msg = response.choices[0].message
                
                # Handle message object for logging/history
                msg_dict = msg.model_dump()
                messages.append(msg_dict) # Use dict for history consistency if needed, but SDK objects work too. 
                                          # Wait, previous core.py used dict. Let's stick to object if SDK supports it 
                                          # or convert. OpenAI SDK usually wants objects or dicts. 
                                          # Let's use dict for logging and appending to keep it clean.
                
                # Log Response
                llm_logger.info(f"--- Sub-Agent Step {step+1} Response ---")
                llm_logger.info(json.dumps(msg_dict, ensure_ascii=False, indent=2))
                
                agent_logger.info(f"Sub-Agent Content: {msg.content}")

                # Check for tool calls
                if msg.tool_calls:
                    agent_logger.info(f"Sub-Agent requested {len(msg.tool_calls)} tools")
                    for tc in msg.tool_calls:
                        func_name = tc.function.name
                        args = json.loads(tc.function.arguments)
                        agent_logger.info(f"Executing {func_name} with args: {tc.function.arguments}")
                        
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
                            agent_logger.info("Search completed (results logged to search_results.log)")
                            
                        elif func_name == "extract_tool":
                            url = args.get("url")
                            logger.info(f"Sub-Agent extracting: {url}")
                            result = self._extract_tool(url)
                            
                            # Log full extraction results to dedicated logger
                            search_logger.info(f"--- Extract Results for '{url}' ---")
                            search_logger.info(json.dumps(result, ensure_ascii=False, indent=2))
                            
                            agent_logger.info("Extraction completed (results logged to search_results.log)")
                        
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": func_name,
                            "content": json.dumps(result, ensure_ascii=False)
                        })
                else:
                    # No tool calls, check for final response in content
                    content = msg.content or ""
                    match = re.search(r"<FINAL_RESPONSE>(.*?)</FINAL_RESPONSE>", content, re.DOTALL)
                    if match:
                        try:
                            final_json = json.loads(match.group(1).strip())
                            agent_logger.info(f"Sub-Agent Final Response: {json.dumps(final_json, ensure_ascii=False)}")
                            return final_json
                        except json.JSONDecodeError:
                            logger.error("Failed to parse final JSON from sub-agent.")
                            agent_logger.error("Failed to parse final JSON from sub-agent.")
                            return {"summary": content, "sources": []} # Fallback
                    
                    # If no final tag but loop is ending or just chatter?
                    # If it's the last step and no final response, try to return content
                    if step == self.max_steps - 1:
                         agent_logger.warning("Max steps reached without <FINAL_RESPONSE>")
                         return {"summary": content, "sources": []}

            except Exception as e:
                logger.error(f"Sub-agent step failed: {e}")
                agent_logger.error(f"Sub-agent step failed: {e}")
                return {"error": str(e)}

        return {"error": "Max steps reached without final response."}
