import pytest
from unittest.mock import patch, mock_open, MagicMock
import os
import json
import csv
from sync_dashboard import load_product_costs, load_freight_costs, fetch_woocommerce_orders, sync_data

def test_load_product_costs():
    csv_data = "sku,product_name,cost_price\nSKU1,Product 1,120.50\nSKU2,Product 2,invalid_cost\n"
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=csv_data)):
            costs = load_product_costs()
            assert costs == {"SKU1": 120.50}

def test_load_freight_costs():
    csv_data = "AWB Number,Freight Total Amount,Order Number\n12345,150.00,402-123\n"
    with patch("os.path.exists", return_value=True):
        with patch("os.listdir", return_value=["freight_invoice.csv"]):
            with patch("builtins.open", mock_open(read_data=csv_data)):
                freight = load_freight_costs()
                assert freight["12345"] == 150.00
                assert freight["402-123"] == 150.00

@patch("requests.get")
def test_fetch_woocommerce_orders_success(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": 9310, "status": "completed", "total": "749.00"}]
    mock_get.return_value = mock_response
    
    with patch.dict('os.environ', {
        'WOOCOMMERCE_STORE_URL': 'https://liriya.com',
        'WOOCOMMERCE_CONSUMER_KEY': 'x',
        'WOOCOMMERCE_CONSUMER_SECRET': 'x'
    }):
        orders = fetch_woocommerce_orders()
        assert len(orders) == 1
        assert orders[0]["id"] == 9310

@patch("sync_dashboard.fetch_amazon_all_orders")
@patch("sync_dashboard.get_order_items_details")
@patch("sync_dashboard.fetch_order_finances")
@patch("sync_dashboard.fetch_woocommerce_orders")
@patch("sync_dashboard.load_sync_state")
@patch("sync_dashboard.load_product_costs")
@patch("sync_dashboard.load_freight_costs")
@patch("builtins.open", new_callable=mock_open)
def test_sync_data_success(
    mock_open_file,
    mock_load_freight,
    mock_load_costs,
    mock_load_state,
    mock_fetch_woo,
    mock_fetch_finances,
    mock_fetch_items,
    mock_fetch_amazon
):
    # Mock data sets
    mock_load_costs.return_value = {"SKU1": 100.0}
    mock_load_freight.return_value = {"402-123": 50.0}
    mock_load_state.return_value = {"orders": []}
    
    mock_fetch_amazon.return_value = [
        {"amazon_order_id": "402-123", "status": "Shipped", "created_at": "2026-05-18T10:00:00Z"}
    ]
    mock_fetch_items.return_value = [{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}]
    mock_fetch_finances.return_value = {"total_amazon_fees": 125.0, "total_refunds": 0.0}
    mock_fetch_woo.return_value = []
    
    with patch("os.makedirs"):
        sync_data()
        
        # Verify JSON write was called
        mock_open_file.assert_called_with("server/dashboard_data.json", "w")
        
        # Verify summary output values in written json
        handle = mock_open_file()
        written_data = "".join(call[0][0] for call in handle.write.call_args_list)
        parsed = json.loads(written_data)
        
        assert parsed["summary"]["total_orders"] == 1
        assert parsed["summary"]["total_revenue"] == 500.0
        # Profit = 500 (rev) - 100 (cost) - 125 (fees) - 50 (ship) = 225
        assert parsed["summary"]["total_profit"] == 225.0


@patch("requests.get")
def test_fetch_woocommerce_orders_pagination(mock_get):
    mock_response1 = MagicMock()
    mock_response1.status_code = 200
    mock_response1.json.return_value = [{"id": i, "status": "completed", "total": "100.00"} for i in range(100)]
    
    mock_response2 = MagicMock()
    mock_response2.status_code = 200
    mock_response2.json.return_value = [{"id": 100 + i, "status": "completed", "total": "100.00"} for i in range(5)]
    
    mock_get.side_effect = [mock_response1, mock_response2]
    
    with patch.dict('os.environ', {
        'WOOCOMMERCE_STORE_URL': 'https://liriya.com',
        'WOOCOMMERCE_CONSUMER_KEY': 'x',
        'WOOCOMMERCE_CONSUMER_SECRET': 'x'
    }):
        orders = fetch_woocommerce_orders()
        assert len(orders) == 105
        assert mock_get.call_count == 2
        # Verify page query param was incremented
        args1, kwargs1 = mock_get.call_args_list[0]
        args2, kwargs2 = mock_get.call_args_list[1]
        assert kwargs1["params"]["page"] == 1
        assert kwargs2["params"]["page"] == 2


@patch("sync_dashboard.fetch_amazon_all_orders")
@patch("sync_dashboard.get_order_items_details")
@patch("sync_dashboard.fetch_order_finances")
@patch("sync_dashboard.fetch_woocommerce_orders")
@patch("sync_dashboard.load_sync_state")
@patch("sync_dashboard.load_product_costs")
@patch("sync_dashboard.load_freight_costs")
@patch("builtins.open", new_callable=mock_open)
def test_sync_data_detailed_scenarios(
    mock_open_file,
    mock_load_freight,
    mock_load_costs,
    mock_load_state,
    mock_fetch_woo,
    mock_fetch_finances,
    mock_fetch_items,
    mock_fetch_amazon
):
    # Mock data sets
    # Product costs
    mock_load_costs.return_value = {
        "SKU1": 100.0,
        "SKU2": 200.0
    }
    
    # Freight costs (one matched by Amazon ID, one matched by WooCommerce ID, one matched by AWB)
    mock_load_freight.return_value = {
        "402-shipped-fees": 40.0,
        "402-shipped-fallback": 30.0,
        "AWB-WOO-SHIPPED": 25.0
    }
    
    # Sync state for AWB fallback lookup (WooCommerce order matches AWB lookup in state)
    mock_load_state.return_value = {
        "orders": [
            {"amazon_order_id": "9310", "awb_number": "AWB-WOO-SHIPPED"}
        ]
    }
    
    # Amazon orders: 1 shipped with finances, 1 shipped with fallback fees (finances return 0), 1 canceled
    mock_fetch_amazon.return_value = [
        {"amazon_order_id": "402-shipped-fees", "status": "Shipped", "created_at": "2026-05-18T10:00:00Z"},
        {"amazon_order_id": "402-shipped-fallback", "status": "Shipped", "created_at": "2026-05-18T11:00:00Z"},
        {"amazon_order_id": "402-canceled", "status": "Canceled", "created_at": "2026-05-19T10:00:00Z"}
    ]
    
    # Order items details:
    # 402-shipped-fees -> 1x SKU1 (price 500)
    # 402-shipped-fallback -> 1x SKU2 (price 800)
    # 402-canceled -> 1x SKU1 (price 500)
    def mock_items_details(order_id):
        if order_id == "402-shipped-fees":
            return [{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}]
        elif order_id == "402-shipped-fallback":
            return [{"sku": "SKU2", "price": 800.0, "quantity": 1, "title": "Item 2"}]
        elif order_id == "402-canceled":
            return [{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}]
        return []
    mock_fetch_items.side_effect = mock_items_details
    
    # Order finances:
    # 402-shipped-fees -> 150.0 fees
    # 402-shipped-fallback -> 0.0 fees (triggering 25% fallback)
    # 402-canceled -> should not be called, but return 0
    def mock_finances(order_id):
        if order_id == "402-shipped-fees":
            return {"total_amazon_fees": 150.0, "total_refunds": 0.0}
        return {"total_amazon_fees": 0.0, "total_refunds": 0.0}
    mock_fetch_finances.side_effect = mock_finances
    
    # WooCommerce orders: 1 shipped, 1 canceled
    mock_fetch_woo.return_value = [
        {
            "id": 9310,
            "status": "completed",
            "date_created": "2026-05-20T12:00:00",
            "total": "600.00",
            "line_items": [{"sku": "SKU1", "quantity": 1, "price": "600.00", "name": "Item 1"}]
        },
        {
            "id": 9311,
            "status": "cancelled",
            "date_created": "2026-05-20T13:00:00",
            "total": "400.00",
            "line_items": [{"sku": "SKU2", "quantity": 1, "price": "400.00", "name": "Item 2"}]
        }
    ]
    
    with patch("os.makedirs"):
        with patch("time.sleep"):  # Avoid actual sleep(2) in sync_data
            sync_data()
        
        # Verify JSON write was called
        mock_open_file.assert_called_with("server/dashboard_data.json", "w")
        
        # Verify summary output values in written json
        handle = mock_open_file()
        written_data = "".join(call[0][0] for call in handle.write.call_args_list)
        parsed = json.loads(written_data)
        
        summary = parsed["summary"]
        # Total orders (3 Amazon + 2 Woo = 5)
        assert summary["total_orders"] == 5
        # Shipped: (2 Amazon + 1 Woo = 3)
        assert summary["total_shipped"] == 3
        # Cancelled: (1 Amazon + 1 Woo = 2)
        assert summary["total_cancelled"] == 2
        
        # Revenue calculations:
        # Shipped Amazon: 500 + 800 = 1300
        # Cancelled Amazon: 0
        # Shipped Woo: 600
        # Cancelled Woo: 0
        # Total Revenue = 1300 + 600 = 1900.0
        assert summary["total_revenue"] == 1900.0
        
        # Amazon Fees calculations:
        # 402-shipped-fees: 150.0
        # 402-shipped-fallback: 200.0 (800 * 0.25)
        # 402-canceled: 0
        # Woo Shipped Fees (Payment Gateway 2%): 12.0 (600 * 0.02)
        # Woo Cancelled: 0
        # Total Amazon & Gateway Fees = 150.0 + 200.0 + 12.0 = 362.0
        assert summary["total_amazon_fees"] == 362.0
        
        # Shipping Cost calculations:
        # 402-shipped-fees: 40.0
        # 402-shipped-fallback: 30.0
        # 402-canceled: 0.0
        # Woo Shipped: 25.0
        # Woo Cancelled: 0.0
        # Total Shipping Cost = 40.0 + 30.0 + 25.0 = 95.0
        assert summary["total_shipping_cost"] == 95.0
        
        # Profit calculations:
        # 402-shipped-fees: 500 (rev) - 100 (cost) - 150 (fees) - 40 (shipping) = 210.0
        # 402-shipped-fallback: 800 (rev) - 200 (cost) - 200 (fees) - 30 (shipping) = 370.0
        # 402-canceled: 0.0
        # Woo Shipped: 600 (rev) - 100 (cost) - 12 (fees) - 25 (shipping) = 463.0
        # Woo Cancelled: 0.0
        # Total Profit = 210.0 + 370.0 + 463.0 = 1043.0
        assert summary["total_profit"] == 1043.0
