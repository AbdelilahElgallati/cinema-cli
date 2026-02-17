"""Simple logging utility for cinema-cli application."""
import os
from datetime import datetime
from src.config import APP_LOG


def log_event(category: str, message: str, level: str = "INFO"):
    """Log an event to the application log file.
    
    Args:
        category: Event category (e.g., 'download', 'playback', 'api')
        message: Log message
        level: Log level (INFO, WARNING, ERROR, DEBUG)
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] [{category}] {message}\n"
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(APP_LOG), exist_ok=True)
        
        with open(APP_LOG, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        # Silently fail - logging should never crash the app
        pass
