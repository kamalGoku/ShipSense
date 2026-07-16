import logging
import os
from logging.handlers import RotatingFileHandler

import config
from logger import get_logger, _ROOT_NAME


def _own_handlers(logger):
    """Handlers installed by logger.py itself (pytest's logging plugin also
    attaches capture handlers; exclude anything from _pytest)."""
    return [h for h in logger.handlers
            if not type(h).__module__.startswith("_pytest")]


def test_get_logger_returns_shipsense_child():
    logger = get_logger("some_module")
    assert logger.name == f"{_ROOT_NAME}.some_module"
    # Children carry no handlers of their own; they propagate to the root
    assert logger.handlers == []
    assert logger.propagate is True


def test_get_logger_does_not_double_prefix():
    logger = get_logger(f"{_ROOT_NAME}.already_prefixed")
    assert logger.name == f"{_ROOT_NAME}.already_prefixed"


def test_root_has_single_handler_pair():
    get_logger("trigger_configuration")
    root = logging.getLogger(_ROOT_NAME)

    own = _own_handlers(root)
    stream_handlers = [h for h in own if type(h) is logging.StreamHandler]
    file_handlers = [h for h in own if isinstance(h, RotatingFileHandler)]
    assert len(stream_handlers) == 1
    assert len(file_handlers) == 1
    assert len(own) == 2


def test_repeated_get_logger_adds_no_handlers():
    logger1 = get_logger("dup_check")
    root = logging.getLogger(_ROOT_NAME)
    count = len(_own_handlers(root))

    logger2 = get_logger("dup_check")
    assert logger1 is logger2
    assert len(_own_handlers(root)) == count == 2
    assert logger2.handlers == []


def test_handler_format():
    get_logger("fmt_check")
    root = logging.getLogger(_ROOT_NAME)
    for handler in _own_handlers(root):
        formatter = handler.formatter
        assert formatter is not None
        assert "%(asctime)s" in formatter._fmt
        assert "%(levelname)-7s" in formatter._fmt
        assert "%(filename)s:%(lineno)d" in formatter._fmt


def test_log_file_lives_under_config_log_dir():
    """The single RotatingFileHandler writes inside config.LOG_DIR (redirected
    to a temp dir by conftest before the first import — nothing under the
    repo's logs/)."""
    get_logger("path_check")
    root = logging.getLogger(_ROOT_NAME)
    file_handler = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
    assert os.path.dirname(file_handler.baseFilename) == os.path.abspath(config.LOG_DIR)


def test_messages_reach_the_shared_file():
    logger = get_logger("write_check")
    logger.info("logger smoke test message")

    root = logging.getLogger(_ROOT_NAME)
    file_handler = next(h for h in root.handlers if isinstance(h, RotatingFileHandler))
    file_handler.flush()
    with open(file_handler.baseFilename, "r", encoding="utf-8") as f:
        assert "logger smoke test message" in f.read()
