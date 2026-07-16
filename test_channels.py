import os
from dotenv import load_dotenv
load_dotenv()
from shiprocket_api import _get_api_token, API_BASE
import requests

token = _get_api_token(os.getenv("SHIPROCKET_EMAIL"), os.getenv("SHIPROCKET_PASSWORD"))
resp = requests.get(f"{API_BASE}/orders", headers={"Authorization": f"Bearer {token}"}, params={"page": 1, "per_page": 50})
data = resp.json()
for o in data.get("data", []):
    print(f"Order: {o.get('id')} | Status: {o.get('status')} | Channel Name: {o.get('channel_name')} | Channel ID: {o.get('channel_id')} | Customer: {o.get('customer_name')}")
