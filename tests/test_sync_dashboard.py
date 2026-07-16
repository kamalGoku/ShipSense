import json
import os
import pytest
import requests
from unittest.mock import patch, MagicMock

import config
import sync_dashboard
from sync_dashboard import (
    load_product_costs,
    load_freight_costs,
    fetch_woocommerce_orders,
    sync_data,
    _sync_amazon_to_db,
    _normalize_amazon_order,
)
from freight_db import FreightDB
from order_db import OrderDB


WOO_ENV = {
    'WOOCOMMERCE_STORE_URL': 'https://liriya.example',
    'WOOCOMMERCE_CONSUMER_KEY': 'x',
    'WOOCOMMERCE_CONSUMER_SECRET': 'x',
}


def _read_output():
    with open(sync_dashboard.DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _ok_json(data):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


# ── loaders ─────────────────────────────────────────────────────────────

def test_load_product_costs(tmp_path):
    # cwd is tmp_path (see conftest); COSTS_FILE is a relative path.
    with open(sync_dashboard.COSTS_FILE, "w", encoding="utf-8") as f:
        f.write("sku,product_name,cost_price\nSKU1,Product 1,120.50\nSKU2,Product 2,invalid_cost\n")
    costs = load_product_costs()
    assert costs == {"SKU1": 120.50}


def test_load_product_costs_missing_file():
    assert load_product_costs() == {}


def test_load_freight_costs_reads_from_db():
    """load_freight_costs is DB-backed now (the CSV era is over)."""
    db = FreightDB()
    db.insert_freight(
        channel_order_id="402-123", shiprocket_order_id="900",
        awb_number="12345", freight_amount=150.0,
    )
    freight = load_freight_costs()
    assert freight["12345"] == 150.0
    assert freight["402-123"] == 150.0


# ── fetch_woocommerce_orders ────────────────────────────────────────────

@patch("requests.get")
def test_fetch_woocommerce_orders_success(mock_get):
    mock_get.return_value = _ok_json(
        [{"id": 9310, "status": "completed", "total": "749.00"}]
    )
    with patch.dict('os.environ', WOO_ENV):
        orders = fetch_woocommerce_orders()
        assert len(orders) == 1
        assert orders[0]["id"] == 9310


@patch("requests.get")
def test_fetch_woocommerce_orders_pagination(mock_get):
    page1 = _ok_json([{"id": i, "status": "completed", "total": "100.00"} for i in range(100)])
    page2 = _ok_json([{"id": 100 + i, "status": "completed", "total": "100.00"} for i in range(5)])
    mock_get.side_effect = [page1, page2]

    with patch.dict('os.environ', WOO_ENV):
        orders = fetch_woocommerce_orders()
        assert len(orders) == 105
        assert mock_get.call_count == 2
        args1, kwargs1 = mock_get.call_args_list[0]
        args2, kwargs2 = mock_get.call_args_list[1]
        assert kwargs1["params"]["page"] == 1
        assert kwargs2["params"]["page"] == 2


def test_fetch_woocommerce_orders_raises_without_credentials(monkeypatch):
    for var in WOO_ENV:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError):
        fetch_woocommerce_orders()


@patch("requests.get")
def test_fetch_woocommerce_orders_raises_on_http_error(mock_get):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 500
    resp.raise_for_status.side_effect = requests.HTTPError("500", response=resp)
    mock_get.return_value = resp
    with patch.dict('os.environ', WOO_ENV):
        with pytest.raises(requests.HTTPError):
            fetch_woocommerce_orders()


# ── watermark semantics ─────────────────────────────────────────────────

def test_watermark_not_advanced_when_amazon_fetch_raises():
    order_db = OrderDB()
    with patch.object(order_db, "set_last_synced_timestamp", wraps=order_db.set_last_synced_timestamp) as spy:
        with patch("sync_dashboard.fetch_amazon_all_orders", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(requests.ConnectionError):
                _sync_amazon_to_db(order_db)
        spy.assert_not_called()
    assert order_db.get_last_synced_timestamp("amazon") is None


def test_watermark_advanced_on_successful_amazon_sync():
    order_db = OrderDB()
    with patch.object(order_db, "set_last_synced_timestamp", wraps=order_db.set_last_synced_timestamp) as spy:
        with patch("sync_dashboard.fetch_amazon_all_orders", return_value=[
            {"amazon_order_id": "402-1", "status": "Shipped", "created_at": "2026-05-18T10:00:00Z"},
        ]), patch("sync_dashboard.get_order_items_details", return_value=[
            {"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"},
        ]), patch("sync_dashboard.fetch_order_finances", return_value={
            "fees": 100.0, "refunds": 0.0, "fetched": True,
        }), patch("sync_dashboard.time.sleep"):
            _sync_amazon_to_db(order_db)
        spy.assert_called_once()
        assert spy.call_args[0][1] == "amazon"
    assert order_db.get_last_synced_timestamp("amazon") is not None


def test_watermark_not_advanced_when_per_order_upsert_fails_mid_delta():
    """One order in the delta failing must hold the watermark back."""
    order_db = OrderDB()

    def items_for(order_id):
        if order_id == "402-bad":
            return []  # triggers "no order items returned by SP-API"
        return [{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}]

    with patch.object(order_db, "set_last_synced_timestamp", wraps=order_db.set_last_synced_timestamp) as spy:
        with patch("sync_dashboard.fetch_amazon_all_orders", return_value=[
            {"amazon_order_id": "402-good", "status": "Shipped", "created_at": "2026-05-18T10:00:00Z"},
            {"amazon_order_id": "402-bad", "status": "Shipped", "created_at": "2026-05-18T11:00:00Z"},
        ]), patch("sync_dashboard.get_order_items_details", side_effect=items_for), \
             patch("sync_dashboard.fetch_order_finances", return_value={
                 "fees": 100.0, "refunds": 0.0, "fetched": True,
             }), patch("sync_dashboard.time.sleep"):
            _sync_amazon_to_db(order_db)
        spy.assert_not_called()

    assert order_db.get_last_synced_timestamp("amazon") is None
    # The good order was still cached (upserts are idempotent, refetch is safe)
    assert order_db.get_order("402-good") is not None


# ── sync_data end-to-end (mocked at fetch boundaries) ───────────────────

def _run_sync(amazon_orders=None, woo_orders=None, items=None, finances=None,
              amazon_exc=None, woo_exc=None):
    """Run sync_data with the platform fetchers mocked at the module boundary."""
    amazon_mock = (
        MagicMock(side_effect=amazon_exc) if amazon_exc
        else MagicMock(return_value=amazon_orders or [])
    )
    woo_mock = (
        MagicMock(side_effect=woo_exc) if woo_exc
        else MagicMock(return_value=woo_orders or [])
    )
    items_mock = (
        MagicMock(side_effect=items) if callable(items)
        else MagicMock(return_value=items if items is not None else [])
    )
    finances_mock = (
        MagicMock(side_effect=finances) if callable(finances)
        else MagicMock(return_value=finances if finances is not None
                       else {"fees": 0.0, "refunds": 0.0, "fetched": True})
    )
    with patch("sync_dashboard.fetch_amazon_all_orders", amazon_mock), \
         patch("sync_dashboard.fetch_woocommerce_orders", woo_mock), \
         patch("sync_dashboard.get_order_items_details", items_mock), \
         patch("sync_dashboard.fetch_order_finances", finances_mock), \
         patch("sync_dashboard.fetch_api_freight_costs", return_value={}), \
         patch("sync_dashboard.time.sleep"):
        sync_data()
    return _read_output()


def test_sync_data_success():
    with open(sync_dashboard.COSTS_FILE, "w", encoding="utf-8") as f:
        f.write("sku,product_name,cost_price\nSKU1,Product 1,100.0\n")
    FreightDB().insert_freight(
        channel_order_id="402-123", shiprocket_order_id="1",
        awb_number=None, freight_amount=50.0,
    )

    out = _run_sync(
        amazon_orders=[{"amazon_order_id": "402-123", "status": "Shipped",
                        "created_at": "2026-05-18T10:00:00Z"}],
        items=[{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}],
        finances={"fees": 125.0, "refunds": 0.0, "fetched": True},
    )

    assert out["summary"]["total_orders"] == 1
    assert out["summary"]["total_revenue"] == 500.0
    # Profit = 500 (rev) - 100 (cost) - 125 (fees) - 50 (ship) = 225
    assert out["summary"]["total_profit"] == 225.0
    assert out["orders"][0]["fees_estimated"] is False


def test_sync_data_first_run_woo_orders_in_same_run_output():
    """Empty DB → sync → the just-fetched Woo orders appear in THIS run's
    output (cache is read after both upserts)."""
    out = _run_sync(
        amazon_orders=[],
        woo_orders=[{
            "id": 9310,
            "status": "completed",
            "date_created": "2026-05-20T12:00:00",
            "date_modified": "2026-05-20T12:00:00",
            "line_items": [{"sku": "SKU1", "quantity": 1, "price": "600.00",
                            "total": "600.00", "name": "Item 1"}],
        }],
    )

    woo_ids = [o["amazon_order_id"] for o in out["orders"]
               if o["platform"] == config.WOO_PLATFORM_KEY]
    assert "9310" in woo_ids
    assert out["summary"]["total_orders"] == 1
    assert out["summary"]["total_revenue"] == 600.0


def test_sync_data_renders_cached_data_when_amazon_fetch_fails():
    # Pre-populate the cache with an already-synced order
    OrderDB().upsert_order(
        order_id="402-cached", platform=config.AMAZON_PLATFORM_KEY,
        status="Shipped", created_at="2026-05-18T10:00:00Z",
        updated_at="2026-05-18T10:00:00Z",
        items=[{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}],
        finances={"fees": 100.0, "refunds": 0.0, "fetched": True},
    )

    out = _run_sync(amazon_exc=requests.ConnectionError("boom"))

    assert out["summary"]["total_orders"] == 1
    assert out["orders"][0]["amazon_order_id"] == "402-cached"
    # Watermark untouched
    assert OrderDB().get_last_synced_timestamp("amazon") is None


def test_sync_data_detailed_scenarios():
    with open(sync_dashboard.COSTS_FILE, "w", encoding="utf-8") as f:
        f.write("sku,product_name,cost_price\nSKU1,Product 1,100.0\nSKU2,Product 2,200.0\n")

    fdb = FreightDB()
    fdb.insert_freight(channel_order_id="402-shipped-fees", shiprocket_order_id="1",
                       awb_number=None, freight_amount=40.0)
    fdb.insert_freight(channel_order_id="402-shipped-fallback", shiprocket_order_id="2",
                       awb_number=None, freight_amount=30.0)
    fdb.insert_freight(channel_order_id=None, shiprocket_order_id="3",
                       awb_number="AWB-WOO-SHIPPED", freight_amount=25.0)

    # sync_state maps the Woo order to its AWB for freight lookup
    import state_manager
    state_manager.save_state({"orders": [
        {"shiprocket_order_id": "3", "amazon_order_id": "9310",
         "awb_number": "AWB-WOO-SHIPPED"},
    ]})

    # A legacy cached order whose finances were never really fetched
    # (no 'fetched' flag, zero fees) → estimated-fees path in normalize.
    OrderDB().upsert_order(
        order_id="402-shipped-fallback", platform=config.AMAZON_PLATFORM_KEY,
        status="Shipped", created_at="2026-05-18T11:00:00Z",
        updated_at="2026-05-18T11:00:00Z",
        items=[{"sku": "SKU2", "price": 800.0, "quantity": 1, "title": "Item 2"}],
        finances={"total_amazon_fees": 0.0, "total_refunds": 0.0},
    )

    def items_for(order_id):
        return {
            "402-shipped-fees": [{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}],
            "402-canceled": [{"sku": "SKU1", "price": 500.0, "quantity": 1, "title": "Item 1"}],
        }.get(order_id, [])

    def finances_for(order_id):
        if order_id == "402-shipped-fees":
            return {"fees": 150.0, "refunds": 0.0, "fetched": True}
        # Legacy fallback order re-fetch also fails this run; its cached
        # legacy record still renders with estimated fees.
        raise requests.HTTPError("500")

    out = _run_sync(
        amazon_orders=[
            {"amazon_order_id": "402-shipped-fees", "status": "Shipped", "created_at": "2026-05-18T10:00:00Z"},
            # In the delta too: its finances re-fetch fails, exercising both
            # the estimated-fees render AND the watermark hold-back.
            {"amazon_order_id": "402-shipped-fallback", "status": "Shipped", "created_at": "2026-05-18T11:00:00Z"},
            {"amazon_order_id": "402-canceled", "status": "Canceled", "created_at": "2026-05-19T10:00:00Z"},
        ],
        woo_orders=[
            {
                "id": 9310, "status": "completed",
                "date_created": "2026-05-20T12:00:00",
                "line_items": [{"sku": "SKU1", "quantity": 1, "price": "600.00",
                                "total": "600.00", "name": "Item 1"}],
            },
            {
                "id": 9311, "status": "cancelled",
                "date_created": "2026-05-20T13:00:00",
                "line_items": [{"sku": "SKU2", "quantity": 1, "price": "400.00",
                                "total": "400.00", "name": "Item 2"}],
            },
        ],
        items=items_for,
        finances=finances_for,
    )

    summary = out["summary"]
    assert summary["total_orders"] == 5
    assert summary["total_shipped"] == 3
    assert summary["total_cancelled"] == 2
    # Revenue: 500 + 800 (Amazon shipped) + 600 (Woo shipped) = 1900
    assert summary["total_revenue"] == 1900.0
    # Fees: 150 real + 200 estimated (800*0.25) + 12 gateway (600*0.02) = 362
    assert summary["total_amazon_fees"] == 362.0
    # Shipping: 40 + 30 + 25 = 95
    assert summary["total_shipping_cost"] == 95.0
    # Profit: (500-100-150-40) + (800-200-200-30) + (600-100-12-25) = 1043
    assert summary["total_profit"] == 1043.0

    by_id = {o["amazon_order_id"]: o for o in out["orders"]}
    assert by_id["402-shipped-fees"]["fees_estimated"] is False
    assert by_id["402-shipped-fallback"]["fees_estimated"] is True

    # The failed finances fetch means the Amazon watermark did NOT advance
    assert OrderDB().get_last_synced_timestamp("amazon") is None
    assert OrderDB().get_last_synced_timestamp("woocommerce") is not None


# ── full/partial refund branch (via the normalize/aggregate path) ───────

def _cached_amazon_order(order_id, refunds, fees=50.0, price=500.0):
    return {
        "order_id": order_id,
        "platform": config.AMAZON_PLATFORM_KEY,
        "status": "Shipped",
        "created_at": "2026-05-18T10:00:00Z",
        "updated_at": "2026-05-18T10:00:00Z",
        "items": [{"sku": "SKU1", "price": price, "quantity": 1, "title": "Item 1"}],
        "finances": {"fees": fees, "refunds": refunds, "fetched": True},
    }


def test_full_refund_zeroes_cogs_and_accrues_cancellation_fees():
    """Refund within FULL_REFUND_TOLERANCE of sale price zeroes COGS and
    the order's fees roll into platform cancellation_fees."""
    o = OrderDB()
    cached = _cached_amazon_order("402-full", refunds=500.0)
    o.upsert_order(order_id=cached["order_id"], platform=cached["platform"],
                   status=cached["status"], created_at=cached["created_at"],
                   updated_at=cached["updated_at"], items=cached["items"],
                   finances=cached["finances"])

    with open(sync_dashboard.COSTS_FILE, "w", encoding="utf-8") as f:
        f.write("sku,product_name,cost_price\nSKU1,Product 1,100.0\n")

    out = _run_sync(amazon_orders=[])

    order = out["orders"][0]
    assert order["product_cost"] == 0.0            # COGS recovered
    # Profit = 500 - 0 (cogs) - 50 (fees) - 0 (ship) - 500 (refund) = -50
    assert order["profit"] == -50.0
    pb = out["platform_breakdown"][config.AMAZON_PLATFORM_KEY]
    assert pb["cancellation_fees"] == 50.0


def test_full_refund_within_tolerance():
    product_costs = {"SKU1": 100.0}
    cached = _cached_amazon_order(
        "402-tol", refunds=500.0 - config.FULL_REFUND_TOLERANCE
    )
    norm = _normalize_amazon_order(cached, product_costs, {}, {})
    assert norm["is_full_refund"] is True
    assert norm["product_cost"] == 0.0


def test_partial_refund_keeps_cogs():
    product_costs = {"SKU1": 100.0}
    cached = _cached_amazon_order("402-partial", refunds=200.0)
    norm = _normalize_amazon_order(cached, product_costs, {}, {})
    assert norm["is_full_refund"] is False
    assert norm["product_cost"] == 100.0
    # Profit = 500 - 100 - 50 - 0 - 200 = 150
    assert norm["profit"] == 150.0


# ── fees_estimated flag ─────────────────────────────────────────────────

def test_fees_estimated_true_when_finances_unavailable():
    cached = _cached_amazon_order("402-nofin", refunds=0.0)
    cached["finances"] = None  # never fetched
    norm = _normalize_amazon_order(cached, {}, {}, {})
    assert norm["fees_estimated"] is True
    assert norm["fees"] == round(500.0 * config.ESTIMATED_AMAZON_FEE_RATE, 2)


def test_fees_estimated_false_with_real_fees():
    cached = _cached_amazon_order("402-fin", refunds=0.0, fees=75.0)
    norm = _normalize_amazon_order(cached, {}, {}, {})
    assert norm["fees_estimated"] is False
    assert norm["fees"] == 75.0


def test_fees_estimated_false_with_real_zero_fees():
    """A genuinely-zero-fee order (fetched=True) must not fall back to the
    estimate."""
    cached = _cached_amazon_order("402-zerofee", refunds=0.0, fees=0.0)
    norm = _normalize_amazon_order(cached, {}, {}, {})
    assert norm["fees_estimated"] is False
    assert norm["fees"] == 0.0
