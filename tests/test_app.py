import json
import os
import sys

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, mock_open

# Make the server package importable as `app` (matches how it runs in prod)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'server')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app  # noqa: E402

client = TestClient(app)


def _sse_events(response):
    """Parse an SSE body into the list of decoded JSON payloads."""
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_index_route():
    response = client.get("/")
    assert response.status_code == 200  # redirect followed
    assert "text/html" in response.headers["content-type"]


def test_shipments_route():
    response = client.get("/shipments")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_revenue_route():
    response = client.get("/revenue")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_dashboard_api_not_found():
    with patch("os.path.exists", return_value=False):
        response = client.get("/api/dashboard")
        assert response.status_code == 404
        assert response.json() == {"error": "Dashboard data not found. Please sync first."}


def test_dashboard_api_success():
    mock_data = {"summary": {"total_revenue": 100}}
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data='{"summary": {"total_revenue": 100}}')):
            response = client.get("/api/dashboard")
            assert response.status_code == 200
            assert response.json() == mock_data


# ── /api/state ──────────────────────────────────────────────────────────

def test_state_api_missing_file_returns_empty_state():
    response = client.get("/api/state")
    assert response.status_code == 200
    assert response.json() == {"last_sync": None, "orders": []}


def test_state_api_returns_orders(tmp_path):
    import app as app_mod
    state = {"last_sync": "2026-05-01T12:00:00+05:30",
             "orders": [{"shiprocket_order_id": "100", "amazon_order_id": "402-1"}]}
    with open(app_mod.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert data["orders"] == state["orders"]


# ── /api/run: POST-only, JSON SSE protocol ──────────────────────────────

def test_run_api_get_returns_405():
    response = client.get("/api/run")
    assert response.status_code == 405
    assert "error" in response.json()


def test_run_api_post_invalid_cmd_yields_json_log_and_complete():
    response = client.post("/api/run", json={"cmd": "rm -rf /"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(response)
    assert events[0]["event"] == "log"
    assert "Invalid command" in events[0]["text"]
    assert events[-1]["event"] == "complete"
    assert events[-1]["code"] != 0


def test_run_api_post_missing_orders_param():
    response = client.post("/api/run", json={"cmd": "sync-selected-amazon"})
    events = _sse_events(response)
    assert events[0]["event"] == "log"
    assert "Missing orders parameter" in events[0]["text"]
    assert events[-1] == {"event": "complete", "code": 1}


def test_run_api_post_invalid_orders_param():
    response = client.post(
        "/api/run",
        json={"cmd": "sync-selected-amazon", "orders": "402-1;DROP TABLE"},
    )
    events = _sse_events(response)
    assert "Invalid orders parameter" in events[0]["text"]
    assert events[-1]["event"] == "complete"


def test_run_api_rejects_when_lock_held():
    import app as app_mod
    with patch.object(app_mod, "acquire_sync_lock", return_value=None):
        response = client.post("/api/run", json={"cmd": "dry-run"})
        events = _sse_events(response)
        assert events[0] == {"event": "log", "text": "Another sync is already running"}
        assert events[-1] == {"event": "complete", "code": 1}


# ── DASHBOARD_TOKEN auth ────────────────────────────────────────────────

def test_api_requires_token_when_dashboard_token_set(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")

    # No token → 401
    response = client.get("/api/state")
    assert response.status_code == 401

    # Wrong token → 401
    response = client.get("/api/state", headers={"X-Dashboard-Token": "nope"})
    assert response.status_code == 401

    # Correct token via header → 200
    response = client.get("/api/state", headers={"X-Dashboard-Token": "s3cret"})
    assert response.status_code == 200

    # Correct token via query param → 200
    response = client.get("/api/state?token=s3cret")
    assert response.status_code == 200


def test_api_open_when_dashboard_token_unset(monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    response = client.get("/api/state")
    assert response.status_code == 200


def test_static_pages_not_behind_token(monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "s3cret")
    response = client.get("/shipments")
    assert response.status_code == 200


# ── /api/pending-orders ─────────────────────────────────────────────────

@patch('app.fetch_amazon_new_orders')
@patch('app.check_new_orders')
def test_pending_orders_api(mock_check_new_orders, mock_fetch_amazon_new_orders):
    mock_fetch_amazon_new_orders.return_value = [
        {
            "amazon_order_id": "402-1234567-8901234",
            "status": "Unshipped",
            "created_at": "2026-05-17T10:00:00Z",
            "items_unshipped": 2
        }
    ]
    mock_check_new_orders.return_value = [
        {
            "channel_order_id": "LIRIYA_123",
            "status": "NEW",
            "created_at": "2026-05-17T11:00:00Z"
        }
    ]

    response = client.get("/api/pending-orders")
    assert response.status_code == 200

    data = response.json()
    assert "pending_orders" in data
    assert "errors" not in data

    orders = data["pending_orders"]
    assert len(orders) == 2

    # Sorted by date descending (11:00 before 10:00)
    assert orders[0]["source"] == "WooCommerce"
    assert orders[0]["order_id"] == "LIRIYA_123"
    assert orders[1]["source"] == "Amazon"
    assert orders[1]["order_id"] == "402-1234567-8901234"


@patch('app.fetch_amazon_new_orders')
@patch('app.check_new_orders')
def test_pending_orders_api_reports_errors(mock_check_new_orders, mock_fetch_amazon_new_orders):
    """An upstream failure surfaces in the errors field instead of a 500."""
    mock_fetch_amazon_new_orders.side_effect = RuntimeError("SP-API down")
    mock_check_new_orders.return_value = [
        {"channel_order_id": "LIRIYA_9", "status": "NEW",
         "created_at": "2026-05-17T11:00:00Z"},
    ]

    response = client.get("/api/pending-orders")
    assert response.status_code == 200

    data = response.json()
    assert len(data["pending_orders"]) == 1
    assert any("amazon" in e and "SP-API down" in e for e in data["errors"])
