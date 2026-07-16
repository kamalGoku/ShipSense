"""Centralized logging for ShipSense.

A single package root logger ("shipsense") owns the one stream handler and
the one rotating file handler. `get_logger(name)` returns a child logger
(`shipsense.<name>`) with no handlers of its own that propagates to the root.
This guarantees exactly one RotatingFileHandler ever touches the log file —
multiple handlers rotating the same file corrupt it.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

import config

_ROOT_NAME = "shipsense"
_configured = False


def _configure_root() -> None:
    """Attach handlers to the package root logger exactly once."""
    global _configured
    if _configured:
        return

    root = logging.getLogger(_ROOT_NAME)
    if root.handlers:
        # Already configured elsewhere (e.g. across Uvicorn reloads).
        _configured = True
        return

    # Create the log directory lazily (at first get_logger call, not import time)
    os.makedirs(config.LOG_DIR, exist_ok=True)

    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)-7s - %(filename)s:%(lineno)d - %(message)s"
    )

    # Console handler - writes to stderr/stdout to ensure SSE stream capture
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # File handler - 10MB rotation and 5 backups. The ONLY file handler.
    log_file_path = os.environ.get(
        "LOG_FILE_PATH", os.path.join(config.LOG_DIR, "agent.log")
    )
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Don't propagate to the true root logger (avoids duplicate output if the
    # embedding app configured logging.basicConfig).
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the "shipsense" root.

    The child has no handlers of its own; records propagate to the single
    configured root, so all modules share one file handler.
    """
    _configure_root()
    if name == _ROOT_NAME or name.startswith(_ROOT_NAME + "."):
        child_name = name
    else:
        child_name = f"{_ROOT_NAME}.{name}"
    logger = logging.getLogger(child_name)
    logger.propagate = True
    return logger
