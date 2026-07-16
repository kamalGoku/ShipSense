"""Ad-hoc probe: inspect the shape of a Shiprocket order's `shipments` field.

Usage:
    python3 scripts/probe_shiprocket_api.py <shiprocket_order_id>

Requires SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD in the environment (or .env).
"""
import os
import sys

import requests
from dotenv import load_dotenv

API_BASE = "https://apiv2.shiprocket.in/v1/external"


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <shiprocket_order_id>")
        sys.exit(1)
    order_id = sys.argv[1]

    load_dotenv()
    email = os.getenv("SHIPROCKET_EMAIL")
    pwd = os.getenv("SHIPROCKET_PASSWORD")

    auth_resp = requests.post(f"{API_BASE}/auth/login", json={"email": email, "password": pwd})
    token = auth_resp.json().get("token")

    resp = requests.get(f"{API_BASE}/orders/show/{order_id}", headers={"Authorization": f"Bearer {token}"})
    data = resp.json()

    shipments = data.get("data", {}).get("shipments")
    if isinstance(shipments, dict):
        print("Keys in shipments dict:")
        print(shipments.keys())
        for k, v in shipments.items():
            if isinstance(v, dict):
                print(f"Key {k} has id: {v.get('id')}")
            elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                print(f"Key {k} has list with id: {v[0].get('id')}")
    elif isinstance(shipments, list):
        print("Shipments is a list")
        for s in shipments:
            print(s.get("id"))


if __name__ == "__main__":
    main()
