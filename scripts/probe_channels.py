"""Ad-hoc probe: list recent Shiprocket orders with their channel info.

Usage:
    python3 scripts/probe_channels.py

Requires SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD in the environment (or .env).
"""
import os
import sys

import requests
from dotenv import load_dotenv

# Make the repo root importable when run as scripts/probe_channels.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    load_dotenv()
    from shiprocket_api import _get_api_token, API_BASE

    token = _get_api_token(os.getenv("SHIPROCKET_EMAIL"), os.getenv("SHIPROCKET_PASSWORD"))
    resp = requests.get(
        f"{API_BASE}/orders",
        headers={"Authorization": f"Bearer {token}"},
        params={"page": 1, "per_page": 50},
    )
    data = resp.json()
    for o in data.get("data", []):
        print(f"Order: {o.get('id')} | Status: {o.get('status')} | "
              f"Channel Name: {o.get('channel_name')} | Channel ID: {o.get('channel_id')} | "
              f"Customer: {o.get('customer_name')}")


if __name__ == "__main__":
    main()
