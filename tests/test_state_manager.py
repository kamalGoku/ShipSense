import os
import json
import tempfile
import pytest
from state_manager import load_state, save_state, merge_orders, mark_synced, mark_error, mark_printed

@pytest.fixture
def temp_state_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)

def test_load_state_empty(temp_state_file):
    # Should create a new state with empty lists
    if os.path.exists(temp_state_file):
        os.remove(temp_state_file)
        
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
        {"amazon_order_id": "2", "shiprocket_order_id": "101", "awb_number": "AWB-101"}, # Update
        {"amazon_order_id": "3", "shiprocket_order_id": "102"} # New
    ]
    
    added = merge_orders(old_state, new_orders)
    
    assert added == 1
    assert len(old_state["orders"]) == 3
    # Check that order 1 is untouched
    order_1 = next(o for o in old_state["orders"] if o["amazon_order_id"] == "1")
    assert order_1["label_printed"] is True
    
    # Check that order 2 got the new awb_number but kept synced_to_amazon
    order_2 = next(o for o in old_state["orders"] if o["amazon_order_id"] == "2")
    assert order_2["awb_number"] == "AWB-101"
    assert order_2["synced_to_amazon"] is False
    
    # Check that order 3 was added
    order_3 = next(o for o in old_state["orders"] if o["amazon_order_id"] == "3")
    assert order_3["shiprocket_order_id"] == "102"

def test_mark_synced():
    state = {
        "orders": [
            {"amazon_order_id": "1", "shiprocket_order_id": "100", "synced_to_amazon": False}
        ]
    }
    mark_synced(state, "100")
    assert state["orders"][0]["synced_to_amazon"] is True
    assert "synced_at" in state["orders"][0]
    
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
