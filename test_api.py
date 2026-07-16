import requests, os
from dotenv import load_dotenv
load_dotenv()
API_BASE = "https://apiv2.shiprocket.in/v1/external"
email = os.getenv("SHIPROCKET_EMAIL")
pwd = os.getenv("SHIPROCKET_PASSWORD")

auth_resp = requests.post(f"{API_BASE}/auth/login", json={"email": email, "password": pwd})
token = auth_resp.json().get("token")

order_id = "1238801368"
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
