import logging
import os
from logging.handlers import RotatingFileHandler

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

def get_logger(name: str) -> logging.Logger:
    """
    Creates and configures a centralized logger for the application.
    Implements guard logic to prevent duplicate handlers across Uvicorn reloads.
    """
    logger = logging.getLogger(name)
    
    # Only configure handlers if they haven't been added yet
    if not logger.handlers:
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
        
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)-7s - %(filename)s:%(lineno)d - %(message)s"
        )
        
        # Console handler - Writes to stdout to ensure SSE stream capture
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)
        
        # File handler - Writes to logs/agent.log with 10MB rotation and 5 backups
        log_file_path = os.environ.get("LOG_FILE_PATH", "logs/agent.log")
        file_handler = RotatingFileHandler(
            log_file_path, maxBytes=10*1024*1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Prevent propagation to avoid duplicate logs if root logger is also configured
        logger.propagate = False
        
    return logger
