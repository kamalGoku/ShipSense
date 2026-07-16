import logging

import pytest
import requests
from unittest.mock import patch, MagicMock

import shiprocket_api
from shiprocket_api import (
    _fetch_orders,
    auto_assign_couriers,
    _generate_and_download_label,
    fetch_shiprocket_orders,
    scrape_shiprocket_api,
    download_label_for_specific_order,
)


@pytest.fixture
def mock_token():
    with patch("shiprocket_api._get_api_token", return_value="dummy_token") as mock:
        yield mock


def test_fetch_shiprocket_orders_alias():
    """The old scrape_shiprocket_api name is kept as an alias."""
    assert scrape_shiprocket_api is fetch_shiprocket_orders


def test_fetch_orders_amazon_regex(mock_token):
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
                    "channel_order_id": "1234",  # Not Amazon format
                    "status": "NEW",
                    "channel_name": "LIRIYA (WOOCOMMERCE)",
                    "awb_code": "AWB456"
                },
                {
                    "id": 3,
                    "channel_order_id": "402-9999999-9999999",
                    "status": "DELIVERED",  # Excluded status
                    "channel_name": "LIRIYA (AMAZON)"
                }
            ]
        }
        # First page returns data, second page returns empty
        mock_get.side_effect = [mock_response, MagicMock(status_code=200, json=lambda: {"data": []})]

        orders = _fetch_orders("dummy_token")

        assert len(orders) == 1
        assert orders[0]["amazon_order_id"] == "402-1234567-1234567"
        assert orders[0]["awb_number"] == "AWB123"


def test_fetch_orders_raises_on_page_fetch_failure():
    """A non-200 page must raise, never silently return partial data."""
    with patch("requests.get") as mock_get:
        bad = MagicMock()
        bad.status_code = 500
        bad.json.return_value = {"message": "server error"}
        mock_get.return_value = bad

        with pytest.raises(RuntimeError):
            _fetch_orders("dummy_token")


def test_auto_assign_couriers_dry_run(mock_token, caplog):
    caplog.set_level(logging.DEBUG, logger="shipsense")
    with patch("requests.get") as mock_get:
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

        # dry_run must NOT call post
        with patch("requests.post") as mock_post:
            result = auto_assign_couriers("email", "pass", channel_name="LIRIYA (WOOCOMMERCE)", dry_run=True)

            assert result is True
            mock_post.assert_not_called()

            assert "DRY RUN MODE" in caplog.text
            assert "Attempting Delhivery Surface" in caplog.text
            assert "Would schedule pickup" in caplog.text


def test_generate_and_download_label_dry_run(caplog):
    caplog.set_level(logging.DEBUG, logger="shipsense")
    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        path = _generate_and_download_label("token", 500, "9284", dry_run=True)

        mock_post.assert_not_called()
        mock_get.assert_not_called()

        assert path is not None
        assert "9284.pdf" in path
        assert "Would download label" in caplog.text


# ── download_label_for_specific_order: True only on real download ───────

def test_download_label_specific_order_success(mock_token):
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {"data": {"shipments": [{"id": 555}]}}

    with patch("requests.get", return_value=show_resp), \
         patch("shiprocket_api._generate_and_download_label", return_value="/tmp/label.pdf") as mock_gen:
        ok = download_label_for_specific_order("e", "p", "100", "402-1", dry_run=False)
        assert ok is True
        mock_gen.assert_called_once()


def test_download_label_specific_order_false_when_download_fails(mock_token):
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {"data": {"shipments": [{"id": 555}]}}

    with patch("requests.get", return_value=show_resp), \
         patch("shiprocket_api._generate_and_download_label", return_value=None):
        ok = download_label_for_specific_order("e", "p", "100", "402-1", dry_run=False)
        assert ok is False


def test_download_label_specific_order_false_when_no_shipment(mock_token):
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {"data": {"shipments": []}}

    with patch("requests.get", return_value=show_resp):
        ok = download_label_for_specific_order("e", "p", "100", "402-1", dry_run=False)
        assert ok is False


def test_download_label_specific_order_false_on_auth_failure():
    with patch("shiprocket_api._get_api_token", return_value=None):
        ok = download_label_for_specific_order("e", "p", "100", "402-1", dry_run=False)
        assert ok is False
