from benchmark.builtin.prompts import render_template_source

Advanced_MANAGER_PROMPT = render_template_source("core.advanced-multi-agent.manager")
ADVANCED_EXECUTION_PROMPT = render_template_source("core.advanced-multi-agent.execution")
TRADING_AGENT_PROMPT = render_template_source("core.advanced-multi-agent.trading")
NEWS_AGENT_PROMPT = render_template_source("core.advanced-multi-agent.news")
CODER_AGENT_PROMPT = render_template_source("core.advanced-multi-agent.coder")
ANALYST_AGENT_PROMPT = render_template_source("core.advanced-multi-agent.analyst")
CRITIC_AGENT_PROMPT = render_template_source("core.advanced-multi-agent.critic")
