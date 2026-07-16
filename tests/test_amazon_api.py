import pytest
from unittest.mock import patch, MagicMock
import os
from amazon_api import get_lwa_access_token, confirm_shipment_api

def test_get_amazon_lwa_token_success():
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "amzn_dummy_token", "expires_in": 3600}
        mock_post.return_value = mock_response
        with patch.dict('os.environ', {'AMAZON_CLIENT_ID': 'x', 'AMAZON_CLIENT_SECRET': 'x', 'AMAZON_REFRESH_TOKEN': 'x'}):
            token = get_lwa_access_token()
            assert token == "amzn_dummy_token"

def test_confirm_shipment_api_success():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("amazon_api.get_order_items") as mock_items:
            # Mock getting order items (required for confirmation payload)
            mock_items.return_value = [{"orderItemId": "item1", "quantity": 1}]
            
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 204 # 204 No Content is success for SP-API
                mock_post.return_value = mock_response
                
                result = confirm_shipment_api(
                    "402-1234567",
                    "Delhivery",
                    "AWB123",
                    dry_run=False
                )
                
                assert result == "SUCCESS"
                
                # Check that correct carrier name was formatted
                args, kwargs = mock_post.call_args
                payload = kwargs["json"]
                assert payload["packageDetail"]["carrierName"] == "Delhivery"
                assert payload["packageDetail"]["trackingNumber"] == "AWB123"

def test_confirm_shipment_api_failure():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("amazon_api.get_order_items") as mock_items:
            mock_items.return_value = [{"orderItemId": "item1", "quantity": 1}]
            
            with patch("requests.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 400
                mock_response.json.return_value = {"errors": [{"message": "InvalidCarrier"}]}
                mock_post.return_value = mock_response
                
                result = confirm_shipment_api(
                    "402-1234567",
                    "UnknownCarrier",
                    "AWB123",
                    dry_run=False
                )
                
                assert result == "ERROR: InvalidCarrier"

def test_fetch_amazon_all_orders_success():
    from amazon_api import fetch_amazon_all_orders
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "payload": {
                    "Orders": [
                        {
                            "AmazonOrderId": "402-123",
                            "OrderStatus": "Shipped",
                            "PurchaseDate": "2026-05-18T10:00:00Z",
                            "OrderTotal": {"Amount": "100.00", "CurrencyCode": "INR"}
                        }
                    ]
                }
            }
            mock_get.return_value = mock_response
            
            orders = fetch_amazon_all_orders(days_back=7)
            assert len(orders) == 1
            assert orders[0]["amazon_order_id"] == "402-123"
            assert orders[0]["status"] == "Shipped"

def test_get_order_items_details_success():
    from amazon_api import get_order_items_details
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "payload": {
                    "OrderItems": [
                        {
                            "SellerSKU": "SKU123",
                            "ItemPrice": {"Amount": "500.00"},
                            "QuantityOrdered": 2,
                            "Title": "Product 123"
                        }
                    ]
                }
            }
            mock_get.return_value = mock_response
            
            items = get_order_items_details("402-123")
            assert len(items) == 1
            assert items[0]["sku"] == "SKU123"
            assert items[0]["price"] == 500.0
            assert items[0]["quantity"] == 2
            assert items[0]["title"] == "Product 123"

def test_fetch_order_finances_success():
    from amazon_api import fetch_order_finances
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "payload": {
                    "FinancialEvents": {
                        "ShipmentEventList": [
                            {
                                "ShipmentItemList": [
                                    {
                                        "ItemFeeList": [
                                            {
                                                "FeeType": "ReferralFee",
                                                "FeeAmount": {"CurrencyAmount": "-75.00"}
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
            mock_get.return_value = mock_response
            
            finances = fetch_order_finances("402-123")
            assert finances["total_amazon_fees"] == 75.0
            assert finances["total_refunds"] == 0.0


def test_fetch_amazon_all_orders_pagination():
    from amazon_api import fetch_amazon_all_orders
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response1 = MagicMock()
            mock_response1.status_code = 200
            mock_response1.json.return_value = {
                "payload": {
                    "Orders": [
                        {
                            "AmazonOrderId": "402-123",
                            "OrderStatus": "Shipped",
                            "PurchaseDate": "2026-05-18T10:00:00Z",
                            "OrderTotal": {"Amount": "100.00", "CurrencyCode": "INR"}
                        }
                    ],
                    "NextToken": "next_token_value"
                }
            }
            mock_response2 = MagicMock()
            mock_response2.status_code = 200
            mock_response2.json.return_value = {
                "payload": {
                    "Orders": [
                        {
                            "AmazonOrderId": "402-456",
                            "OrderStatus": "Canceled",
                            "PurchaseDate": "2026-05-19T10:00:00Z",
                            "OrderTotal": {"Amount": "200.00", "CurrencyCode": "INR"}
                        }
                    ]
                }
            }
            mock_get.side_effect = [mock_response1, mock_response2]
            
            with patch("time.sleep"):  # Avoid actual sleeping in tests
                orders = fetch_amazon_all_orders(days_back=7)
                
            assert len(orders) == 2
            assert orders[0]["amazon_order_id"] == "402-123"
            assert orders[1]["amazon_order_id"] == "402-456"
            assert mock_get.call_count == 2
            
            # Check call args of the second get request
            args, kwargs = mock_get.call_args_list[1]
            assert kwargs["params"] == {"NextToken": "next_token_value"}


def test_get_order_items_details_rate_limiting():
    from amazon_api import get_order_items_details
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response_429 = MagicMock()
            mock_response_429.status_code = 429
            
            mock_response_200 = MagicMock()
            mock_response_200.status_code = 200
            mock_response_200.json.return_value = {
                "payload": {
                    "OrderItems": [
                        {
                            "SellerSKU": "SKU_LIMIT",
                            "ItemPrice": {"Amount": "150.00"},
                            "QuantityOrdered": 1,
                            "Title": "Limited Product"
                        }
                    ]
                }
            }
            
            mock_get.side_effect = [mock_response_429, mock_response_429, mock_response_200]
            
            with patch("time.sleep") as mock_sleep:
                items = get_order_items_details("402-limit")
                assert len(items) == 1
                assert items[0]["sku"] == "SKU_LIMIT"
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(3)
                mock_sleep.assert_any_call(6)


def test_fetch_order_finances_rate_limiting():
    from amazon_api import fetch_order_finances
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response_429 = MagicMock()
            mock_response_429.status_code = 429
            
            mock_response_200 = MagicMock()
            mock_response_200.status_code = 200
            mock_response_200.json.return_value = {
                "payload": {
                    "FinancialEvents": {
                        "ShipmentEventList": []
                    }
                }
            }
            
            mock_get.side_effect = [mock_response_429, mock_response_200]
            
            with patch("time.sleep") as mock_sleep:
                finances = fetch_order_finances("402-limit")
                assert finances["total_amazon_fees"] == 0.0
                assert mock_sleep.call_count == 1
                mock_sleep.assert_called_with(3)


def test_fetch_order_finances_not_found():
    from amazon_api import fetch_order_finances
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            
            # Simulate raising HTTPError on raise_for_status
            from requests.exceptions import HTTPError
            http_err = HTTPError("Not Found", response=mock_response)
            mock_get.return_value.raise_for_status.side_effect = http_err
            mock_get.return_value.status_code = 404
            
            finances = fetch_order_finances("402-notfound")
            assert finances["total_amazon_fees"] == 0.0
            assert finances["total_refunds"] == 0.0


def test_fetch_order_finances_refunds_and_adjustments():
    from amazon_api import fetch_order_finances
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "payload": {
                    "FinancialEvents": {
                        "ShipmentEventList": [
                            {
                                "ShipmentItemList": [
                                    {
                                        "ItemFeeList": [
                                            {"FeeAmount": {"CurrencyAmount": "-10.00"}}
                                        ]
                                    }
                                ]
                            }
                        ],
                        "RefundEventList": [
                            {
                                "ShipmentItemList": [
                                    {
                                        "ItemFeeList": [
                                            {"FeeAmount": {"CurrencyAmount": "-2.50"}}
                                        ]
                                    }
                                ],
                                "ShipmentItemAdjustmentList": [
                                    {
                                        "ItemChargeAdjustmentList": [
                                            {"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": "-120.00"}}
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
            mock_get.return_value = mock_response
            
            finances = fetch_order_finances("402-refund")
            # total_amazon_fees = abs(-10.00) + abs(-2.50) = 12.5
            # total_refunds = abs(-120.00) = 120.0
            assert finances["total_amazon_fees"] == 12.5
            assert finances["total_refunds"] == 120.0
