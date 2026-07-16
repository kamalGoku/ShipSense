import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

# We need to make sure the server path is accessible
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'server')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app

client = TestClient(app)

def test_index_route():
    # It should redirect to shipments
    response = client.get("/")
    assert response.status_code == 200  # with redirect followed
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
    # When file doesn't exist, should return 404 or empty skeleton
    with patch("os.path.exists", return_value=False):
        response = client.get("/api/dashboard")
        assert response.status_code == 404
        assert response.json() == {"error": "Dashboard data not found. Please sync first."}

def test_dashboard_api_success():
    from unittest.mock import mock_open
    mock_data = {"summary": {"total_revenue": 100}}
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data='{"summary": {"total_revenue": 100}}')):
            response = client.get("/api/dashboard")
            assert response.status_code == 200
            assert response.json() == mock_data

@patch('app.fetch_amazon_new_orders')
@patch('app.check_new_orders')
def test_pending_orders_api(mock_check_new_orders, mock_fetch_amazon_new_orders):
    # Mock Amazon response
    mock_fetch_amazon_new_orders.return_value = [
        {
            "amazon_order_id": "402-1234567-8901234",
            "status": "Unshipped",
            "created_at": "2026-05-17T10:00:00Z",
            "items_unshipped": 2
        }
    ]
    
    # Mock Shiprocket response
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
    
    orders = data["pending_orders"]
    assert len(orders) == 2
    
    # Orders should be sorted by date descending (11:00 before 10:00)
    assert orders[0]["source"] == "WooCommerce"
    assert orders[0]["order_id"] == "LIRIYA_123"
    
    assert orders[1]["source"] == "Amazon"
    assert orders[1]["order_id"] == "402-1234567-8901234"
