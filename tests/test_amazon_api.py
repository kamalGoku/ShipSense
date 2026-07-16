import pytest
import requests
from unittest.mock import patch, MagicMock

import config
from amazon_api import (
    get_lwa_access_token,
    confirm_shipment_api,
    map_carrier_to_spapi,
    _spapi_get,
    fetch_amazon_all_orders,
    fetch_amazon_new_orders,
    fetch_order_finances,
    get_order_items_details,
)


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    if status_code >= 400:
        err = requests.HTTPError(f"{status_code} error", response=resp)
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_get_amazon_lwa_token_success():
    with patch("requests.post") as mock_post:
        mock_post.return_value = _mock_response(
            200, {"access_token": "amzn_dummy_token", "expires_in": 3600}
        )
        with patch.dict('os.environ', {'AMAZON_CLIENT_ID': 'x', 'AMAZON_CLIENT_SECRET': 'x', 'AMAZON_REFRESH_TOKEN': 'x'}):
            token = get_lwa_access_token()
            assert token == "amzn_dummy_token"


def test_confirm_shipment_api_success():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("amazon_api.get_order_items") as mock_items:
            mock_items.return_value = [{"orderItemId": "item1", "quantity": 1}]

            with patch("requests.post") as mock_post:
                mock_post.return_value = _mock_response(204)

                result = confirm_shipment_api(
                    "402-1234567", "Delhivery", "AWB123", dry_run=False
                )

                assert result == "SUCCESS"
                args, kwargs = mock_post.call_args
                payload = kwargs["json"]
                assert payload["packageDetail"]["carrierName"] == "Delhivery"
                assert payload["packageDetail"]["trackingNumber"] == "AWB123"


def test_confirm_shipment_api_failure():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("amazon_api.get_order_items") as mock_items:
            mock_items.return_value = [{"orderItemId": "item1", "quantity": 1}]

            with patch("requests.post") as mock_post:
                mock_post.return_value = _mock_response(
                    400, {"errors": [{"message": "InvalidCarrier"}]}
                )

                result = confirm_shipment_api(
                    "402-1234567", "UnknownCarrier", "AWB123", dry_run=False
                )

                assert result == "ERROR: InvalidCarrier"


# ── map_carrier_to_spapi (config.CARRIER_SPAPI_MAP driven) ──────────────

@pytest.mark.parametrize("courier,expected", [
    ("Delhivery Surface", ("Delhivery", "Delhivery", "Delhivery Surface")),
    ("Blue Dart Air", ("BlueDart", "BlueDart", "Blue Dart Air")),
    ("BLUEDART EXPRESS", ("BlueDart", "BlueDart", "BLUEDART EXPRESS")),
    ("Ecom Express Surface", ("Ecom Express", "Ecom Express", "Ecom Express Surface")),
    ("Xpressbees 1kg", ("Xpressbees", "Xpressbees", "Xpressbees 1kg")),
    ("Ekart Logistics", ("Ekart", "Ekart", "Ekart Logistics")),
    # Unknown carrier falls back to "Others" with the raw name as carrierName
    ("India Post", ("Others", "India Post", "Standard")),
])
def test_map_carrier_to_spapi(courier, expected):
    assert map_carrier_to_spapi(courier) == expected


# ── _spapi_get 429 retry / exhaustion ───────────────────────────────────

def test_spapi_get_429_exhaustion_raises():
    """429 forever must raise after exactly config.RATE_LIMIT_RETRIES attempts."""
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get, patch("amazon_api.time.sleep") as mock_sleep:
            mock_get.return_value = _mock_response(429)

            with pytest.raises(requests.HTTPError):
                _spapi_get("https://example.test/endpoint")

            assert mock_get.call_count == config.RATE_LIMIT_RETRIES
            # A sleep between each attempt, none after the last one
            assert mock_sleep.call_count == config.RATE_LIMIT_RETRIES - 1


def test_spapi_get_429_then_success_retries():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get, patch("amazon_api.time.sleep") as mock_sleep:
            mock_get.side_effect = [
                _mock_response(429),
                _mock_response(429),
                _mock_response(200, {"payload": {}}),
            ]
            resp = _spapi_get("https://example.test/endpoint")
            assert resp.status_code == 200
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(config.RATE_LIMIT_BACKOFF_SECONDS)
            mock_sleep.assert_any_call(config.RATE_LIMIT_BACKOFF_SECONDS * 2)


# ── fetch_amazon_all_orders ─────────────────────────────────────────────

def test_fetch_amazon_all_orders_success():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {
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
            })

            orders = fetch_amazon_all_orders(days_back=7)
            assert len(orders) == 1
            assert orders[0]["amazon_order_id"] == "402-123"
            assert orders[0]["status"] == "Shipped"
            assert orders[0]["total"] == 100.0


def test_fetch_amazon_all_orders_pagination():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            page1 = _mock_response(200, {
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
            })
            page2 = _mock_response(200, {
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
            })
            mock_get.side_effect = [page1, page2]

            with patch("amazon_api.time.sleep"):
                orders = fetch_amazon_all_orders(days_back=7)

            assert len(orders) == 2
            assert orders[0]["amazon_order_id"] == "402-123"
            assert orders[1]["amazon_order_id"] == "402-456"
            assert mock_get.call_count == 2

            args, kwargs = mock_get.call_args_list[1]
            assert kwargs["params"] == {"NextToken": "next_token_value"}


def test_fetch_amazon_all_orders_raises_on_failure():
    """Fetch failures must RAISE, never return a partial/empty list."""
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError("network down")
            with pytest.raises(requests.ConnectionError):
                fetch_amazon_all_orders(days_back=7)


def test_fetch_amazon_all_orders_raises_on_rate_limit_exhaustion():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get, patch("amazon_api.time.sleep"):
            mock_get.return_value = _mock_response(429)
            with pytest.raises(requests.HTTPError):
                fetch_amazon_all_orders(days_back=7)


# ── fetch_amazon_new_orders ─────────────────────────────────────────────

def test_fetch_amazon_new_orders_paginates_next_token():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            page1 = _mock_response(200, {
                "payload": {
                    "Orders": [
                        {
                            "AmazonOrderId": "402-111",
                            "OrderStatus": "Unshipped",
                            "PurchaseDate": "2026-05-18T10:00:00Z",
                            "OrderTotal": {"Amount": "100.00"},
                            "NumberOfItemsUnshipped": 2
                        }
                    ],
                    "NextToken": "tok"
                }
            })
            page2 = _mock_response(200, {
                "payload": {
                    "Orders": [
                        {
                            "AmazonOrderId": "402-222",
                            "OrderStatus": "PartiallyShipped",
                            "PurchaseDate": "2026-05-18T11:00:00Z",
                            "OrderTotal": {"Amount": "50.00"},
                            "NumberOfItemsUnshipped": 1
                        }
                    ]
                }
            })
            mock_get.side_effect = [page1, page2]

            with patch("amazon_api.time.sleep"):
                orders = fetch_amazon_new_orders()

            assert [o["amazon_order_id"] for o in orders] == ["402-111", "402-222"]
            assert mock_get.call_count == 2
            args, kwargs = mock_get.call_args_list[1]
            assert kwargs["params"] == {"NextToken": "tok"}


def test_fetch_amazon_new_orders_raises_on_failure():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(500)
            with pytest.raises(requests.HTTPError):
                fetch_amazon_new_orders()


# ── get_order_items_details ─────────────────────────────────────────────

def test_get_order_items_details_success():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {
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
            })

            items = get_order_items_details("402-123")
            assert len(items) == 1
            assert items[0]["sku"] == "SKU123"
            assert items[0]["price"] == 500.0
            assert items[0]["quantity"] == 2
            assert items[0]["title"] == "Product 123"


def test_get_order_items_details_rate_limiting():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            ok = _mock_response(200, {
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
            })
            mock_get.side_effect = [_mock_response(429), _mock_response(429), ok]

            with patch("amazon_api.time.sleep") as mock_sleep:
                items = get_order_items_details("402-limit")
                assert len(items) == 1
                assert items[0]["sku"] == "SKU_LIMIT"
                assert mock_sleep.call_count == 2
                mock_sleep.assert_any_call(config.RATE_LIMIT_BACKOFF_SECONDS)
                mock_sleep.assert_any_call(config.RATE_LIMIT_BACKOFF_SECONDS * 2)


# ── fetch_order_finances: {"fees","refunds","fetched"} contract ─────────

def test_fetch_order_finances_success_sets_fetched():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {
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
            })

            finances = fetch_order_finances("402-123")
            assert finances == {"fees": 75.0, "refunds": 0.0, "fetched": True}


def test_fetch_order_finances_404_returns_zeros_fetched():
    """404 means no financial events posted yet: real zeros, fetched=True."""
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(404)

            finances = fetch_order_finances("402-notfound")
            assert finances == {"fees": 0.0, "refunds": 0.0, "fetched": True}


def test_fetch_order_finances_500_raises():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(500)

            with pytest.raises(requests.HTTPError):
                fetch_order_finances("402-servererr")


def test_fetch_order_finances_rate_limiting():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            ok = _mock_response(200, {
                "payload": {"FinancialEvents": {"ShipmentEventList": []}}
            })
            mock_get.side_effect = [_mock_response(429), ok]

            with patch("amazon_api.time.sleep") as mock_sleep:
                finances = fetch_order_finances("402-limit")
                assert finances["fees"] == 0.0
                assert finances["fetched"] is True
                assert mock_sleep.call_count == 1
                mock_sleep.assert_called_with(config.RATE_LIMIT_BACKOFF_SECONDS)


def test_fetch_order_finances_refunds_and_adjustments():
    with patch("amazon_api.get_lwa_access_token", return_value="amzn_dummy_token"):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _mock_response(200, {
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
            })

            finances = fetch_order_finances("402-refund")
            # fees = abs(-10.00) + abs(-2.50) = 12.5, refunds = abs(-120.00)
            assert finances["fees"] == 12.5
            assert finances["refunds"] == 120.0
            assert finances["fetched"] is True
