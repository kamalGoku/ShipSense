import os
import json
import tempfile
import pytest
from state_manager import (
    load_state, save_state, merge_orders,
    mark_synced, mark_error, mark_printed,
)


@pytest.fixture
def temp_state_file(tmp_path):
    return str(tmp_path / "state.json")


def test_load_state_empty(temp_state_file):
    state = load_state(temp_state_file)
    assert "orders" in state
    assert len(state["orders"]) == 0
    assert "last_sync" in state


def test_save_and_load_state(temp_state_file):
    state = {"last_sync": "2026-05-01T12:00:00+05:30", "orders": [{"amazon_order_id": "123"}]}
    save_state(state, temp_state_file)

    loaded = load_state(temp_state_file)
    assert len(loaded["orders"]) == 1
    assert loaded["orders"][0]["amazon_order_id"] == "123"


def test_merge_orders():
    old_state = {
        "orders": [
            {"amazon_order_id": "1", "shiprocket_order_id": "100", "label_printed": True},
            {"amazon_order_id": "2", "shiprocket_order_id": "101", "synced_to_amazon": False}
        ]
    }
    new_orders = [
        {"amazon_order_id": "2", "shiprocket_order_id": "101", "awb_number": "AWB-101"},  # Update
        {"amazon_order_id": "3", "shiprocket_order_id": "102"}  # New
    ]

    added = merge_orders(old_state, new_orders)

    assert added == 1
    assert len(old_state["orders"]) == 3
    # Order 1 untouched
    order_1 = next(o for o in old_state["orders"] if o["amazon_order_id"] == "1")
    assert order_1["label_printed"] is True

    # Order 2 got the new awb_number but kept synced_to_amazon
    order_2 = next(o for o in old_state["orders"] if o["amazon_order_id"] == "2")
    assert order_2["awb_number"] == "AWB-101"
    assert order_2["synced_to_amazon"] is False

    # Order 3 added
    order_3 = next(o for o in old_state["orders"] if o["amazon_order_id"] == "3")
    assert order_3["shiprocket_order_id"] == "102"


def test_merge_orders_awb_reassignment_resets_flags_and_error():
    """A changed AWB means the shipment was reassigned: re-push to Amazon,
    reprint the label, and drop the stale error."""
    state = {
        "orders": [
            {
                "shiprocket_order_id": "100",
                "amazon_order_id": "402-1",
                "courier_name": "Delhivery",
                "awb_number": "AWB-OLD",
                "synced_to_amazon": True,
                "synced_at": "2026-05-01T12:00:00+05:30",
                "label_printed": True,
                "printed_at": "2026-05-01T13:00:00+05:30",
                "error": "some stale error",
            }
        ]
    }
    added = merge_orders(state, [
        {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
         "awb_number": "AWB-NEW", "courier_name": "Blue Dart"},
    ])

    assert added == 0
    o = state["orders"][0]
    assert o["awb_number"] == "AWB-NEW"
    assert o["courier_name"] == "Blue Dart"
    assert o["synced_to_amazon"] is False
    assert o["synced_at"] is None
    assert o["label_printed"] is False
    assert o["printed_at"] is None
    assert o["error"] is None


def test_merge_orders_same_awb_keeps_flags():
    state = {
        "orders": [
            {
                "shiprocket_order_id": "100",
                "amazon_order_id": "402-1",
                "courier_name": "Delhivery",
                "awb_number": "AWB-1",
                "synced_to_amazon": True,
                "label_printed": True,
                "error": "old error",
            }
        ]
    }
    merge_orders(state, [
        {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
         "awb_number": "AWB-1", "courier_name": "Delhivery"},
    ])
    o = state["orders"][0]
    assert o["synced_to_amazon"] is True
    assert o["label_printed"] is True
    # A refresh clears stale errors even without an AWB change
    assert o["error"] is None


def test_merge_orders_invalid_new_awb_does_not_reset():
    state = {
        "orders": [
            {
                "shiprocket_order_id": "100",
                "amazon_order_id": "402-1",
                "awb_number": "AWB-1",
                "synced_to_amazon": True,
                "label_printed": True,
            }
        ]
    }
    for bad_awb in (None, "", "None", "No AWB"):
        merge_orders(state, [
            {"shiprocket_order_id": "100", "awb_number": bad_awb},
        ])
        o = state["orders"][0]
        assert o["awb_number"] == "AWB-1"
        assert o["synced_to_amazon"] is True
        assert o["label_printed"] is True


def test_merge_orders_int_vs_str_id_dedup():
    """An int shiprocket_order_id must dedup against the stored string id
    (str() coercion on both sides) — no duplicate entries."""
    state = {
        "orders": [
            {"shiprocket_order_id": "100", "amazon_order_id": "402-1",
             "awb_number": "AWB-1", "synced_to_amazon": True},
        ]
    }
    added = merge_orders(state, [
        {"shiprocket_order_id": 100, "amazon_order_id": "402-1",
         "awb_number": "AWB-1"},
    ])
    assert added == 0
    assert len(state["orders"]) == 1

    # And the reverse: int stored, string incoming
    state2 = {
        "orders": [
            {"shiprocket_order_id": 200, "amazon_order_id": "402-2",
             "awb_number": "AWB-2", "synced_to_amazon": True},
        ]
    }
    added2 = merge_orders(state2, [
        {"shiprocket_order_id": "200", "amazon_order_id": "402-2",
         "awb_number": "AWB-2"},
    ])
    assert added2 == 0
    assert len(state2["orders"]) == 1


def test_mark_synced():
    state = {
        "orders": [
            {"amazon_order_id": "1", "shiprocket_order_id": "100", "synced_to_amazon": False}
        ]
    }
    mark_synced(state, "100")
    assert state["orders"][0]["synced_to_amazon"] is True
    assert "synced_at" in state["orders"][0]


def test_mark_synced_int_id():
    state = {
        "orders": [
            {"amazon_order_id": "1", "shiprocket_order_id": 100, "synced_to_amazon": False}
        ]
    }
    mark_synced(state, "100")
    assert state["orders"][0]["synced_to_amazon"] is True


def test_mark_error():
    state = {
        "orders": [
            {"amazon_order_id": "1", "shiprocket_order_id": "100", "synced_to_amazon": False}
        ]
    }
    mark_error(state, "100", "InvalidCarrier")
    assert state["orders"][0]["synced_to_amazon"] is False
    assert state["orders"][0]["error"] == "InvalidCarrier"


def test_mark_printed():
    state = {
        "orders": [
            {"amazon_order_id": "1"}
        ]
    }
    mark_printed(state, "1")
    assert state["orders"][0].get("label_printed") is True
    assert "printed_at" in state["orders"][0]
