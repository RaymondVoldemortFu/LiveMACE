import logging
import logging.handlers
import os
import sys

def setup_logging():
    """
    Setup logging configuration
    - Output to console
    - Output to file (rotated daily)
    - Separate error logs
    - Separate LLM trace logs
    - Separate Agent decision logs
    """
    # Create logs directory if it doesn't exist
    # backend/config/logging_config.py -> backend/logs
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Define formatters
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # Info Log File Handler
    info_log_file = os.path.join(log_dir, "app.log")
    info_handler = logging.handlers.TimedRotatingFileHandler(
        info_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    info_handler.setFormatter(formatter)
    info_handler.setLevel(logging.INFO)
    root_logger.addHandler(info_handler)

    # Error Log File Handler
    error_log_file = os.path.join(log_dir, "error.log")
    error_handler = logging.handlers.TimedRotatingFileHandler(
        error_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_handler)

    # --- Specialized Loggers ---

    # 1. LLM Trace Logger (Independent file, no propagation)
    llm_trace_logger = logging.getLogger("llm_trace")
    llm_trace_logger.setLevel(logging.INFO)
    llm_trace_logger.propagate = False
    
    llm_log_file = os.path.join(log_dir, "llm_trace.log")
    llm_handler = logging.handlers.TimedRotatingFileHandler(
        llm_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    llm_handler.setFormatter(formatter)
    llm_trace_logger.addHandler(llm_handler)

    # 2. Agent Decision Logger (Independent file, no propagation)
    # Note: Simple output will still go to root logger (console/app.log) via manual calls in code
    agent_logger = logging.getLogger("agent_decision")
    agent_logger.setLevel(logging.INFO)
    agent_logger.propagate = False
    
    agent_log_file = os.path.join(log_dir, "agent_decision.log")
    agent_handler = logging.handlers.TimedRotatingFileHandler(
        agent_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    agent_handler.setFormatter(formatter)
    agent_logger.addHandler(agent_handler)

    # 3. Search Results Logger (Independent file, no propagation)
    search_logger = logging.getLogger("search_results")
    search_logger.setLevel(logging.INFO)
    search_logger.propagate = False
    
    search_log_file = os.path.join(log_dir, "search_results.log")
    search_handler = logging.handlers.TimedRotatingFileHandler(
        search_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    search_handler.setFormatter(formatter)
    search_logger.addHandler(search_handler)

    # 4. Docker Exec Logger (Independent file, no propagation)
    docker_logger = logging.getLogger("docker_exec")
    docker_logger.setLevel(logging.INFO)
    docker_logger.propagate = False
    
    docker_log_file = os.path.join(log_dir, "docker_exec.log")
    docker_handler = logging.handlers.TimedRotatingFileHandler(
        docker_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    docker_handler.setFormatter(formatter)
    docker_logger.addHandler(docker_handler)

    # 5. Trade Execution Logger (Independent file, no propagation)
    trade_logger = logging.getLogger("trade_execution")
    trade_logger.setLevel(logging.INFO)
    trade_logger.propagate = False
    
    trade_log_file = os.path.join(log_dir, "trade_execution.log")
    trade_handler = logging.handlers.TimedRotatingFileHandler(
        trade_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    trade_handler.setFormatter(formatter)
    trade_logger.addHandler(trade_handler)

    # 6. Tool Selection Logger (Independent file, no propagation)
    tool_selection_logger = logging.getLogger("tool_selection")
    tool_selection_logger.setLevel(logging.INFO)
    tool_selection_logger.propagate = False

    tool_selection_log_file = os.path.join(log_dir, "tool_selection.log")
    tool_selection_handler = logging.handlers.TimedRotatingFileHandler(
        tool_selection_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    tool_selection_handler.setFormatter(formatter)
    tool_selection_logger.addHandler(tool_selection_handler)

    # 7. Tool Output Logger (Independent file, no propagation)
    tool_output_logger = logging.getLogger("tool_output")
    tool_output_logger.setLevel(logging.INFO)
    tool_output_logger.propagate = False

    tool_output_log_file = os.path.join(log_dir, "tool_output.log")
    tool_output_handler = logging.handlers.TimedRotatingFileHandler(
        tool_output_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    tool_output_handler.setFormatter(formatter)
    tool_output_logger.addHandler(tool_output_handler)

    # 8. Tool Selector Trace Logger (Independent file, no propagation)
    tool_selector_trace_logger = logging.getLogger("tool_selector_trace")
    tool_selector_trace_logger.setLevel(logging.INFO)
    tool_selector_trace_logger.propagate = False

    tool_selector_trace_log_file = os.path.join(log_dir, "tool_selector_trace.log")
    tool_selector_trace_handler = logging.handlers.TimedRotatingFileHandler(
        tool_selector_trace_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    tool_selector_trace_handler.setFormatter(formatter)
    tool_selector_trace_logger.addHandler(tool_selector_trace_handler)

    # Uvicorn loggers integration
    logging.getLogger("uvicorn").handlers = []
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.error").handlers = []
    
    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.access").propagate = True
    logging.getLogger("uvicorn.error").propagate = True

    # Log initialization
    logging.info(f"Logging initialized. Logs directory: {log_dir}")
