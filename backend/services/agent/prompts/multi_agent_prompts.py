from benchmark.builtin.prompts import read_builtin_template

MANAGER_PROMPT = read_builtin_template("multi-agent/manager.txt")
TRADING_AGENT_PROMPT = read_builtin_template("multi-agent/trading.txt")
NEWS_AGENT_PROMPT = read_builtin_template("multi-agent/news.txt")
CODER_AGENT_PROMPT = read_builtin_template("multi-agent/coder.txt")
