import pytest
from unittest.mock import patch, MagicMock
from shiprocket_api import _fetch_orders, auto_assign_couriers, _generate_and_download_label

@pytest.fixture
def mock_token():
    with patch("shiprocket_api._get_api_token", return_value="dummy_token") as mock:
        yield mock

def test_fetch_orders_amazon_regex(mock_token):
    # Test that _fetch_orders correctly uses the regex and channel filtering
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "id": 1,
                    "channel_order_id": "402-1234567-1234567",
                    "status": "NEW",
                    "channel_name": "LIRIYA (AMAZON)",
                    "awb_code": "AWB123",
                    "courier_name": "Delhivery"
                },
                {
                    "id": 2,
                    "channel_order_id": "1234", # Not Amazon format
                    "status": "NEW",
                    "channel_name": "LIRIYA (WOOCOMMERCE)",
                    "awb_code": "AWB456"
                },
                {
                    "id": 3,
                    "channel_order_id": "402-9999999-9999999",
                    "status": "DELIVERED", # Excluded status
                    "channel_name": "LIRIYA (AMAZON)"
                }
            ]
        }
        # First page returns data, second page returns empty
        mock_get.side_effect = [mock_response, MagicMock(status_code=200, json=lambda: {"data": []})]
        
        orders = _fetch_orders("dummy_token")
        
        # Only order 1 should be fetched (matching regex, active status, amazon channel)
        assert len(orders) == 1
        assert orders[0]["amazon_order_id"] == "402-1234567-1234567"
        assert orders[0]["awb_number"] == "AWB123"

def test_auto_assign_couriers_dry_run(mock_token, capsys):
    # Test dry-run mode for courier assignment
    with patch("requests.get") as mock_get:
        # Mock orders
        orders_resp = MagicMock()
        orders_resp.status_code = 200
        orders_resp.json.return_value = {
            "data": [
                {
                    "id": 100,
                    "channel_order_id": "9284",
                    "status": "NEW",
                    "channel_name": "LIRIYA (WOOCOMMERCE)",
                    "shipments": [{"id": 500}]
                }
            ]
        }
        
        # Mock serviceability
        serv_resp = MagicMock()
        serv_resp.status_code = 200
        serv_resp.json.return_value = {
            "data": {
                "available_courier_companies": [
                    {"courier_name": "Delhivery Surface", "courier_company_id": 43}
                ]
            }
        }
        
        mock_get.side_effect = [orders_resp, serv_resp]
        
        # We don't mock post, because dry_run should NOT call post
        with patch("requests.post") as mock_post:
            result = auto_assign_couriers("email", "pass", channel_name="LIRIYA (WOOCOMMERCE)", dry_run=True)
            
            assert result is True
            mock_post.assert_not_called()
            
            captured = capsys.readouterr()
            assert "DRY RUN MODE" in captured.out
            assert "Attempting Delhivery Surface" in captured.out
            assert "Would schedule pickup" in captured.out

def test_generate_and_download_label_dry_run(capsys):
    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        _generate_and_download_label("token", 500, "9284", dry_run=True)
        
        mock_post.assert_not_called()
        mock_get.assert_not_called()
        
        captured = capsys.readouterr()
        assert "Would download label" in captured.out
