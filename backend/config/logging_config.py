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
    
    # Clear existing handlers to avoid duplicates if re-initialized
    # Note: This might remove uvicorn's default handlers if called after uvicorn setup,
    # but usually we want to override them or add to them.
    # If we want to keep existing handlers (like uvicorn's), we shouldn't clear them blindly,
    # but here we want to enforce our format and destinations.
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)

    # Info Log File Handler (Rotating daily, keep 30 days)
    # Contains INFO and higher (including ERROR)
    info_log_file = os.path.join(log_dir, "app.log")
    info_handler = logging.handlers.TimedRotatingFileHandler(
        info_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    info_handler.setFormatter(formatter)
    info_handler.setLevel(logging.INFO)
    root_logger.addHandler(info_handler)

    # Error Log File Handler (Rotating daily, keep 30 days)
    # Contains only ERROR and CRITICAL
    error_log_file = os.path.join(log_dir, "error.log")
    error_handler = logging.handlers.TimedRotatingFileHandler(
        error_log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)
    root_logger.addHandler(error_handler)

    # Explicitly set uvicorn loggers to propagate or use our handlers
    # Uvicorn configures its own loggers ('uvicorn', 'uvicorn.access', 'uvicorn.error')
    logging.getLogger("uvicorn").handlers = []
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.error").handlers = []
    
    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.access").propagate = True
    logging.getLogger("uvicorn.error").propagate = True

    # Log initialization
    logging.info(f"Logging initialized. Logs directory: {log_dir}")

