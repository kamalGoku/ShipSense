"""
state_manager.py – Read/write the JSON state file for AWB sync tracking.

The state file tracks which Shiprocket orders have been synced to Amazon,
ensuring idempotent re-runs.
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_STATE = {
    "last_sync": None,
    "orders": []
}

STATE_FILE = os.getenv("STATE_FILE_PATH", "./sync_state.json")


def load_state(path: str | None = None) -> dict:
    """Load the state file. Returns default empty state if file doesn't exist."""
    path = path or STATE_FILE
    if not os.path.exists(path):
        return {**DEFAULT_STATE, "orders": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, path: str | None = None) -> None:
    """Atomically write state to disk (write to temp, then rename)."""
    path = path or STATE_FILE
    state["last_sync"] = datetime.now(IST).isoformat()
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def merge_orders(state: dict, new_orders: list[dict]) -> int:
    """
    Upsert orders by shiprocket_order_id.
    Preserves existing synced_to_amazon status.
    Returns count of newly added orders.
    """
    existing_ids = {o["shiprocket_order_id"] for o in state["orders"]}
    added = 0
    for order in new_orders:
        sid = str(order.get("shiprocket_order_id", ""))
        if not sid:
            continue
        if sid not in existing_ids:
            awb = order.get("awb_number")
            courier = order.get("courier_name")
            state["orders"].append({
                "shiprocket_order_id": sid,
                "amazon_order_id": str(order.get("amazon_order_id", "")),
                "courier_name": str(courier) if courier and courier != "None" else "",
                "awb_number": str(awb) if awb and awb != "None" else "",
                "synced_to_amazon": False,
                "synced_at": None,
                "label_printed": False,
                "printed_at": None,
                "error": None,
            })
            existing_ids.add(sid)
            added += 1
        else:
            # Update AWB / courier if they changed, provided they are valid
            for existing in state["orders"]:
                if existing["shiprocket_order_id"] == sid:
                    new_awb = order.get("awb_number")
                    new_courier = order.get("courier_name")
                    
                    if new_awb and str(new_awb) not in ["None", "", "No AWB"]:
                        existing["awb_number"] = str(new_awb)
                    if new_courier and str(new_courier) not in ["None", ""]:
                        existing["courier_name"] = str(new_courier)
                    break
    return added


def get_pending_orders(state: dict) -> list[dict]:
    """Return orders that have NOT been synced to Amazon yet and have valid AWB data."""
    pending = []
    for o in state["orders"]:
        if o.get("synced_to_amazon"):
            continue
            
        awb = str(o.get("awb_number", "")).strip()
        courier = str(o.get("courier_name", "")).strip()
        
        # Valid AWB check: not empty, not "None", not "No AWB"
        if awb and awb not in ["None", "No AWB", ""]:
            # Also ensure we have a courier name
            if courier and courier not in ["None", ""]:
                pending.append(o)
    return pending


def mark_synced(state: dict, shiprocket_order_id: str) -> None:
    """Flag an order as successfully synced to Amazon."""
    for order in state["orders"]:
        if order["shiprocket_order_id"] == str(shiprocket_order_id):
            order["synced_to_amazon"] = True
            order["synced_at"] = datetime.now(IST).isoformat()
            order["error"] = None
            break


def mark_error(state: dict, shiprocket_order_id: str, error_msg: str) -> None:
    """Record an error for an order (does NOT set synced_to_amazon)."""
    for order in state["orders"]:
        if order["shiprocket_order_id"] == str(shiprocket_order_id):
            order["error"] = error_msg
            break


def mark_printed(state: dict, amazon_order_id: str) -> None:
    """Flag an order label as successfully printed."""
    for order in state["orders"]:
        if order["amazon_order_id"] == str(amazon_order_id):
            order["label_printed"] = True
            order["printed_at"] = datetime.now(IST).isoformat()
            break
