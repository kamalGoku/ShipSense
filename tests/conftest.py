"""Shared isolation fixtures for the ShipSense test suite.

Nothing in this suite may touch the repo working tree. All paths that the
source reads from config (state file, DBs, logs, labels) are redirected to
temp directories:

* The environment variables that config.py reads at import time are set
  HERE, at conftest import time, before any test module imports config.
  This guarantees the session-wide defaults (e.g. the log file the single
  RotatingFileHandler is bound to) live under a temp dir.
* An autouse fixture additionally repoints every path to a per-test
  tmp_path, resets the OrderDB/FreightDB singleton registries, resets the
  amazon_api token cache, and chdirs into tmp_path so any accidental
  relative-path write (labels/, server/dashboard_data.json, ...) lands in
  the sandbox instead of the repo.
"""
import logging
import os
import sys
import tempfile

# ── Session-level env redirection: MUST happen before `import config` ──
_SESSION_TMP = tempfile.mkdtemp(prefix="shipsense-tests-")
os.environ["STATE_FILE_PATH"] = os.path.join(_SESSION_TMP, "sync_state.json")
os.environ["ORDERS_DB_PATH"] = os.path.join(_SESSION_TMP, "orders_data.db")
os.environ["FREIGHT_DB_PATH"] = os.path.join(_SESSION_TMP, "freight_data.db")
os.environ["LABELS_DIR"] = os.path.join(_SESSION_TMP, "labels")
os.environ["LOG_DIR"] = os.path.join(_SESSION_TMP, "logs")
os.environ.pop("LOG_FILE_PATH", None)
os.environ.pop("DASHBOARD_TOKEN", None)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pytest  # noqa: E402

import config  # noqa: E402  (imported after env redirection above)


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Per-test isolation: tmp DBs/state/labels, fresh singletons, tmp cwd."""
    import amazon_api
    import state_manager
    from freight_db import FreightDB
    from order_db import OrderDB

    # Fresh singleton registries so each test gets its own DB files.
    OrderDB._instances.clear()
    FreightDB._instances.clear()

    orders_db = str(tmp_path / "orders_data.db")
    freight_db = str(tmp_path / "freight_data.db")
    state_file = str(tmp_path / "sync_state.json")

    monkeypatch.setattr(config, "ORDERS_DB_PATH", orders_db)
    monkeypatch.setattr(config, "FREIGHT_DB_PATH", freight_db)
    monkeypatch.setattr(config, "STATE_FILE_PATH", state_file)
    monkeypatch.setattr(config, "LABELS_DIR", str(tmp_path / "labels"))

    # Module-level copies of config values taken at import time.
    monkeypatch.setattr(state_manager, "STATE_FILE", state_file)
    if "sync_awb" in sys.modules:
        monkeypatch.setattr(sys.modules["sync_awb"], "STATE_FILE", state_file)
    if "app" in sys.modules:
        app_mod = sys.modules["app"]
        monkeypatch.setattr(app_mod, "STATE_FILE", state_file, raising=False)
        monkeypatch.setattr(
            app_mod, "SYNC_LOCK_FILE", str(tmp_path / ".sync.lock"), raising=False
        )

    # Reset the LWA token cache so tests never leak tokens into each other.
    monkeypatch.setattr(amazon_api, "_access_token", None)
    monkeypatch.setattr(amazon_api, "_token_expiry", 0)

    # Any relative-path write (labels/, server/dashboard_data.json, ...)
    # goes into the per-test tmp dir, never the repo working tree.
    monkeypatch.chdir(tmp_path)

    # The "shipsense" root logger is created with propagate=False; flip it on
    # so pytest's caplog (a handler on the true root logger) sees records.
    logging.getLogger("shipsense").propagate = True

    yield

    OrderDB._instances.clear()
    FreightDB._instances.clear()
