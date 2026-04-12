"""Simple logging utility for cinema-cli application."""
import os
import traceback
from datetime import datetime
from src.config import APP_LOG


def log_event(category: str, message: str, level: str = "INFO", correlation_id: str = ""):
    """Log an event to the application log file.
    
    Args:
        category: Event category (e.g., 'download', 'playback', 'api')
        message: Log message
        level: Log level (INFO, WARNING, ERROR, DEBUG)
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        corr = f" [corr={correlation_id}]" if correlation_id else ""
        log_line = f"[{timestamp}] [{level}] [{category}]{corr} {message}\n"
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(APP_LOG), exist_ok=True)
        
        with open(APP_LOG, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        # Silently fail - logging should never crash the app
        pass


def debug(message: str, exc_info: bool = False):
    """Log a debug message, optionally with exception traceback."""
    msg = message
    if exc_info:
        msg += f"\n{traceback.format_exc()}"
    log_event("system", msg, level="DEBUG")


def info(message: str):
    """Log an info message."""
    log_event("system", message, level="INFO")


def warning(message: str):
    """Log a warning message."""
    log_event("system", message, level="WARNING")


def error(message: str, exc_info: bool = False):
    """Log an error message, optionally with exception traceback."""
    msg = message
    if exc_info:
        msg += f"\n{traceback.format_exc()}"
    log_event("system", msg, level="ERROR")
