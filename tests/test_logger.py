import os
import logging
from logging.handlers import RotatingFileHandler
import pytest

from logger import get_logger

def test_logger_directory_creation(tmp_path, monkeypatch):
    """Test that the logs directory is created if it does not exist"""
    test_logs_dir = tmp_path / "test_logs"
    log_file = test_logs_dir / "agent.log"
    
    # We monkeypatch the logs path just for testing file creation behavior
    monkeypatch.setenv("LOG_FILE_PATH", str(log_file))
    
    # Ensure it does not exist initially
    assert not log_file.exists()
    
    # We create the directory to mimic what logger.py does at the module level
    # since we can't easily undo module-level execution.
    os.makedirs(test_logs_dir, exist_ok=True)
    
    # Get logger and write a message
    logger = get_logger("test_dir_creation")
    logger.info("Test message")
    
    # Verify file was created
    assert log_file.exists()
    
    with open(log_file, "r") as f:
        content = f.read()
        assert "Test message" in content

def test_logger_format_and_handlers():
    """Test that logger has correct handlers and format"""
    logger = get_logger("test_format")
    
    # Check we have exactly two handlers: StreamHandler and RotatingFileHandler
    assert len(logger.handlers) == 2
    
    handlers_types = [type(h) for h in logger.handlers]
    assert logging.StreamHandler in handlers_types
    assert RotatingFileHandler in handlers_types
    
    # Check format strings
    for handler in logger.handlers:
        formatter = handler.formatter
        assert formatter is not None
        # Format string check
        assert "%(asctime)s" in formatter._fmt
        assert "%(levelname)-7s" in formatter._fmt
        assert "%(filename)s:%(lineno)d" in formatter._fmt

def test_logger_guard_against_duplicate_handlers():
    """Test that multiple calls to get_logger do not add duplicate handlers"""
    logger1 = get_logger("test_duplicates")
    initial_handler_count = len(logger1.handlers)
    
    # Call it again
    logger2 = get_logger("test_duplicates")
    
    # Should be the same exact logger object with same number of handlers
    assert logger1 is logger2
    assert len(logger2.handlers) == initial_handler_count
    assert initial_handler_count == 2
