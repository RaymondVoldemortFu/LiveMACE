# 智能体架构文档

## 1. 架构概览

本项目采用基于大语言模型（LLM）的分层智能体架构，旨在通过结合即时市场数据、账户状态和外部网络信息，做出自动化的金融交易决策。

系统核心是一个主智能体（Trading Agent），它负责统筹全局，根据输入的投资组合和价格信息进行推理。当需要外部非结构化信息（如新闻、宏观数据）时，主智能体会调用专门的搜索子智能体（Search Sub-Agent）。

## 2. 核心组件

### 2.1 TradingAgent (主智能体)
- **位置**: `backend/services/agent/core.py`
- **职责**: 核心决策大脑。
- **输入**: 
  - `portfolio`: 当前账户持仓和资金状态。
  - `prices`: 关注标的最新市场价格。
- **输出**: JSON 格式的交易决策（包含操作方向、标的、杠杆倍数等）。
- **逻辑**:
  - 维护一个多轮对话的上下文窗口。
  - 根据系统提示词（`SYSTEM_PROMPT`）进行思考。
  - 动态调用工具（如获取行情、搜索信息）。
  - 在工具执行完成后识别终止符 `<TRADE_DONE>` 结束决策循环。
  - 具备最大步数限制（`max_steps`）和超时兜底机制（默认 HOLD）。

### 2.2 SearchSubAgent (搜索子智能体)
- **位置**: `backend/services/agent/sub_agents/search_agent.py`
- **职责**: 专注于信息检索和总结的专家智能体。
- **工具**:
  - `search_tool`: 调用 Tavily API 进行通用或特定领域的搜索。
  - `extract_tool`: 提取特定 URL 的详细内容。
- **逻辑**:
  - 拥有独立的思考循环和系统提示词（`SUB_AGENT_SYSTEM_PROMPT`）。
  - 能够自主决定搜索关键词、搜索深度和提取哪些页面。
  - 最终输出 `<FINAL_RESPONSE>`，向主智能体返回结构化的信息摘要。

### 2.3 EnvWrapper (环境适配层)
- **位置**: `backend/services/agent/env_wrapper.py`
- **职责**: 连接智能体与外部环境/数据库。
- **主要功能**:
  - **工具注册**: 将 Python 函数封装为 OpenAI Tool 格式。
  - **数据获取**: 提供 `get_market_snapshot` (行情) 和 `get_account_state` (账户) 等工具。
  - **子智能体桥接**: 将 `SearchSubAgent` 封装为 `consult_search_agent` 工具供主智能体调用。

### 2.4 LLMClient (模型客户端)
- **位置**: `backend/services/agent/llm_client.py`
- **职责**: 统一的模型调用接口。
- **特点**:
  - 封装 OpenAI SDK。
  - 支持自定义 `base_url`，可适配各种 OpenAI 兼容接口（如 vLLM 或中转服务）。
  - 统一处理消息历史和工具调用参数。

## 3. 决策流程

1.  **初始化**: 外部服务实例化 `TradingAgent`，注入 `LLMClient` 和注册了所有工具的 `ToolRegistry`。
2.  **启动**: 调用 `agent.run(portfolio, prices)`。
3.  **思考循环**:
    *   Agent 构建包含当前状态的 Prompt 发送给 LLM。
    *   LLM 决定是否需要使用工具。
    *   **分支 A (工具调用)**:
        *   如果是查询行情/账户，直接由 `EnvWrapper` 返回数据。
        *   如果是 `consult_search_agent`，则激活 `SearchSubAgent`。子智能体进行多轮自主搜索和总结，将结果返回给主智能体。
    *   **分支 B (最终决策)**:
        *   LLM 在执行完交易工具后输出 `<TRADE_DONE>` 作为终止信号。
4.  **解析**: 系统提取 JSON 决策，进行容错处理（如字段缺失补全），返回给执行系统。

## 4. 类图与交互图

请参考同目录下的 `agent_architecture.mmd` 文件生成的图表。

