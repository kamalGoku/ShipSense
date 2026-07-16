import sys
from types import SimpleNamespace

import pytest
from unittest.mock import patch, MagicMock

# Patch environment variables before importing sync_awb
with patch.dict('os.environ', {
    'SHIPROCKET_EMAIL': 'test@test.com',
    'SHIPROCKET_PASSWORD': 'test',
    'AMAZON_CLIENT_ID': 'test',
    'AMAZON_CLIENT_SECRET': 'test',
    'AMAZON_REFRESH_TOKEN': 'test',
}):
    import sync_awb


CRED_ENV = {
    "AMAZON_CLIENT_ID": "test",
    "AMAZON_CLIENT_SECRET": "test",
    "AMAZON_REFRESH_TOKEN": "test",
}


@pytest.fixture
def mock_state():
    return {
        "last_sync": "never",
        "orders": []
    }


def _run_main(argv, state):
    """Run sync_awb.main() with all external effects mocked; return the mocks."""
    mocks = {}
    with patch.object(sys, 'argv', argv), \
         patch("sync_awb.load_state", return_value=state), \
         patch("sync_awb.save_state") as save_state, \
         patch("sync_awb.auto_assign_couriers") as assign, \
         patch("sync_awb.fetch_shiprocket_orders", return_value=[]) as fetch, \
         patch("sync_awb.confirm_shipment_api", return_value="SUCCESS") as confirm, \
         patch("sync_awb.pdf_tool.process_folder", return_value=[]), \
         patch("sync_awb.SHIPROCKET_EMAIL", "test"), \
         patch("sync_awb.SHIPROCKET_PASSWORD", "test"), \
         patch.dict("os.environ", CRED_ENV):
        sync_awb.main()
        mocks["save_state"] = save_state
        mocks["assign"] = assign
        mocks["fetch"] = fetch
        mocks["confirm"] = confirm
    return mocks


def test_main_liriya_flag(mock_state):
    """--liriya: Phase 0 uses the Woo channel, Phases 1 and 2 are skipped."""
    mocks = _run_main(["sync_awb.py", "--liriya", "--dry-run"], mock_state)

    mocks["assign"].assert_called_once()
    args, kwargs = mocks["assign"].call_args
    assert kwargs.get("channel_name") == "LIRIYA (WOOCOMMERCE)"

    mocks["fetch"].assert_not_called()
    mocks["confirm"].assert_not_called()


def test_main_amazon_flag(mock_state):
    mock_state["orders"] = [
        {"shiprocket_order_id": "1", "amazon_order_id": "A1", "courier_name": "Delhivery",
         "awb_number": "123", "synced_to_amazon": False}
    ]

    mocks = _run_main(["sync_awb.py", "--amazon", "--dry-run"], mock_state)

    mocks["assign"].assert_called_once()
    args, kwargs = mocks["assign"].call_args
    assert kwargs.get("channel_name") == "LIRIYA (AMAZON)"

    mocks["fetch"].assert_called_once()

    mocks["confirm"].assert_called_once()
    args, kwargs = mocks["confirm"].call_args
    assert kwargs.get("amazon_order_id") == "A1"


def _args(**overrides):
    """Build a parsed-args namespace with sync_awb defaults."""
    base = dict(
        dry_run=False, status=False, skip_scrape=False, amazon=True,
        check_new=False, print_order=None, channel="LIRIYA (AMAZON)",
        liriya=False, orders=None, orders_list=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_phase_confirm_amazon_saves_state_per_order():
    """Each successful confirmation persists state immediately (crash-safe):
    2 successful orders → save_state called at least twice."""
    state = {
        "last_sync": None,
        "orders": [
            {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
             "courier_name": "Delhivery", "awb_number": "AWB-1",
             "synced_to_amazon": False},
            {"shiprocket_order_id": "101", "amazon_order_id": "402-2",
             "courier_name": "Delhivery", "awb_number": "AWB-2",
             "synced_to_amazon": False},
        ]
    }

    with patch("sync_awb.confirm_shipment_api", return_value="SUCCESS") as confirm, \
         patch("sync_awb.save_state") as save_state:
        sync_awb.phase_confirm_amazon(state, _args())

    assert confirm.call_count == 2
    assert save_state.call_count >= 2
    assert all(o["synced_to_amazon"] for o in state["orders"])


def test_phase_confirm_amazon_saves_state_on_error_too():
    state = {
        "last_sync": None,
        "orders": [
            {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
             "courier_name": "Delhivery", "awb_number": "AWB-1",
             "synced_to_amazon": False},
        ]
    }
    with patch("sync_awb.confirm_shipment_api", return_value="ERROR: boom"), \
         patch("sync_awb.save_state") as save_state:
        sync_awb.phase_confirm_amazon(state, _args())

    assert save_state.call_count >= 1
    assert state["orders"][0]["synced_to_amazon"] is False
    assert state["orders"][0]["error"] == "ERROR: boom"


def test_phase_confirm_amazon_dry_run_never_saves():
    state = {
        "last_sync": None,
        "orders": [
            {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
             "courier_name": "Delhivery", "awb_number": "AWB-1",
             "synced_to_amazon": False},
        ]
    }
    with patch("sync_awb.confirm_shipment_api", return_value="SUCCESS"), \
         patch("sync_awb.save_state") as save_state:
        sync_awb.phase_confirm_amazon(state, _args(dry_run=True))

    save_state.assert_not_called()
    assert state["orders"][0]["synced_to_amazon"] is False


def test_orders_whitespace_stripping(mock_state):
    """--orders ' A1 , B2 ,, ' → ['A1', 'B2'] (whitespace and empties dropped)."""
    captured = {}

    def capture_confirm_phase(state, args):
        captured["orders_list"] = args.orders_list

    with patch.object(sys, 'argv',
                      ["sync_awb.py", "--amazon", "--skip-scrape",
                       "--orders", " A1 , B2 ,, "]), \
         patch("sync_awb.load_state", return_value=mock_state), \
         patch("sync_awb.save_state"), \
         patch("sync_awb.phase_confirm_amazon", side_effect=capture_confirm_phase), \
         patch("sync_awb.phase_print_labels"), \
         patch("sync_awb.SHIPROCKET_EMAIL", "test"), \
         patch("sync_awb.SHIPROCKET_PASSWORD", "test"), \
         patch.dict("os.environ", CRED_ENV):
        sync_awb.main()

    assert captured["orders_list"] == ["A1", "B2"]


def test_phase_confirm_amazon_orders_filter():
    """--orders limits the pending set (matched on amazon or shiprocket id)."""
    state = {
        "last_sync": None,
        "orders": [
            {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
             "courier_name": "Delhivery", "awb_number": "AWB-1",
             "synced_to_amazon": False},
            {"shiprocket_order_id": "101", "amazon_order_id": "402-2",
             "courier_name": "Delhivery", "awb_number": "AWB-2",
             "synced_to_amazon": False},
        ]
    }
    with patch("sync_awb.confirm_shipment_api", return_value="SUCCESS") as confirm, \
         patch("sync_awb.save_state"):
        sync_awb.phase_confirm_amazon(state, _args(orders_list=["402-2"]))

    confirm.assert_called_once()
    assert confirm.call_args.kwargs["amazon_order_id"] == "402-2"
