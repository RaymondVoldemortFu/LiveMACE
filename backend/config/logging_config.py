import logging
import logging.handlers
import os
import sys
import smtplib
from pathlib import Path
from email.message import EmailMessage
from typing import List

import dotenv


def _parse_recipients(raw: str) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _load_logging_env() -> None:
    """Load backend .env before reading logging-related env vars."""
    backend_dir = Path(__file__).resolve().parent.parent
    backend_env = backend_dir / ".env"
    if backend_env.exists():
        dotenv.load_dotenv(dotenv_path=backend_env, override=False)
        return
    dotenv.load_dotenv(dotenv.find_dotenv(usecwd=True), override=False)


class ErrorEmailHandler(logging.Handler):
    """Send ERROR-level log records via SMTP email."""

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        from_addr: str,
        password: str,
        recipients: List[str],
        subject_prefix: str = "[LiveMACEBench]",
    ):
        super().__init__(level=logging.ERROR)
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.from_addr = from_addr
        self.password = password
        self.recipients = recipients
        self.subject_prefix = subject_prefix

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = EmailMessage()
            message["From"] = self.from_addr
            message["To"] = ", ".join(self.recipients)
            message["Subject"] = f"{self.subject_prefix} ERROR {record.name}"
            message.set_content(self.format(record))

            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=10) as client:
                client.login(self.from_addr, self.password)
                client.send_message(message)
        except Exception:
            self.handleError(record)


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

    _load_logging_env()

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

    # Error email alert handler (optional)
    email_from = (os.getenv("EMAIL_NAME") or "").strip()
    email_auth = (os.getenv("EMAIL_AUTH") or "").strip()
    smtp_server = (os.getenv("SMTP_SERVER") or "").strip()
    smtp_port_raw = (os.getenv("SMTP_PORT") or "465").strip()
    recipients = _parse_recipients(os.getenv("ERROR_EMAIL_TO", ""))

    if email_from and email_auth and smtp_server and recipients:
        try:
            smtp_port = int(smtp_port_raw)
            if smtp_port <= 0:
                raise ValueError("SMTP_PORT must be > 0")
        except Exception as err:
            raise RuntimeError(f"Invalid SMTP_PORT={smtp_port_raw!r}") from err

        email_handler = ErrorEmailHandler(
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            from_addr=email_from,
            password=email_auth,
            recipients=recipients,
        )
        email_handler.setFormatter(formatter)
        root_logger.addHandler(email_handler)
        logging.info("Error email alert enabled, recipients=%s", recipients)
    else:
        missing_keys = []
        if not email_from:
            missing_keys.append("EMAIL_NAME")
        if not email_auth:
            missing_keys.append("EMAIL_AUTH")
        if not smtp_server:
            missing_keys.append("SMTP_SERVER")
        if not recipients:
            missing_keys.append("ERROR_EMAIL_TO")
        logging.info(
            "Error email alert disabled (missing: %s)",
            ",".join(missing_keys) if missing_keys else "unknown",
        )

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

    # 9. Tool Cache Logger (Independent file, no propagation)
    tool_cache_logger = logging.getLogger("tool_cache")
    tool_cache_logger.setLevel(logging.DEBUG)
    tool_cache_logger.propagate = False

    tool_cache_log_file = os.path.join(log_dir, "tool_cache.log")
    tool_cache_handler = logging.handlers.TimedRotatingFileHandler(
        tool_cache_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    tool_cache_handler.setFormatter(formatter)
    tool_cache_handler.setLevel(logging.DEBUG)
    tool_cache_logger.addHandler(tool_cache_handler)

    # 10. LLM Client Logger (Independent file, no propagation)
    llm_client_logger = logging.getLogger("llm_client")
    llm_client_logger.setLevel(logging.INFO)
    llm_client_logger.propagate = False

    llm_client_log_file = os.path.join(log_dir, "llm_client.log")
    llm_client_handler = logging.handlers.TimedRotatingFileHandler(
        llm_client_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    llm_client_handler.setFormatter(formatter)
    llm_client_logger.addHandler(llm_client_handler)
    llm_client_logger.info("LLM client logger initialized")

    # Uvicorn loggers integration
    logging.getLogger("uvicorn").handlers = []
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.error").handlers = []
    
    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.access").propagate = True
    logging.getLogger("uvicorn.error").propagate = True

    # Log initialization
    logging.info(f"Logging initialized. Logs directory: {log_dir}")
