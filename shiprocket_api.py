import requests
import re
import os
from datetime import datetime
from typing import List, Dict

import config
from logger import get_logger

logger = get_logger(__name__)

API_BASE = config.SHIPROCKET_API_BASE
LABEL_DIR = "labels"


def _safe_error_snippet(resp: requests.Response) -> str:
    """
    Build a log-safe description of an error response: status code plus at
    most the first 200 chars of a JSON "message" field. Never includes the
    full response body (it may contain customer PII).
    """
    snippet = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            msg = body.get("message")
            if isinstance(msg, str) and msg:
                snippet = f" - {msg[:200]}"
    except Exception:
        pass
    return f"status {resp.status_code}{snippet}"


def _shiprocket_get(path_or_url: str, token: str, params: dict | None = None,
                    timeout: int | None = None) -> requests.Response:
    """
    Shared GET helper for the Shiprocket API: auth header, timeout, and
    consistent error handling. Raises RuntimeError with a log-safe message
    on network failure or non-200 status.
    """
    url = path_or_url if path_or_url.startswith("http") else f"{API_BASE}/{path_or_url.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(
            url, headers=headers, params=params,
            timeout=timeout if timeout is not None else config.HTTP_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Shiprocket GET {url} failed: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"Shiprocket GET {url} failed ({_safe_error_snippet(resp)})")
    return resp


def fetch_shiprocket_orders(email: str, password: str) -> List[Dict]:
    """
    Authenticate with Shiprocket API and fetch orders with AWB data.
    Raises on authentication or fetch failure.
    """
    token = _get_api_token(email, password)
    if not token:
        raise RuntimeError("Shiprocket authentication failed")

    return _fetch_orders(token)


# Deprecated alias, kept for backward compatibility.
scrape_shiprocket_api = fetch_shiprocket_orders


def auto_assign_couriers(email: str, password: str,
                         channel_name: str = config.AMAZON_CHANNEL_NAME,
                         dry_run: bool = False,
                         target_order_ids: list[str] | None = None) -> bool:
    """
    PHASE 0: Find NEW orders for the specified channel and assign preferred couriers.
    Preference order comes from config.COURIER_PREFERENCE.
    """
    logger.info(f"🚀 Phase 0: Auto-Assigning Couriers for NEW {channel_name} Orders...")
    if dry_run:
        logger.info("   (DRY RUN MODE - no actual assignments will be made)")

    token = _get_api_token(email, password)
    if not token:
        raise RuntimeError("Shiprocket authentication failed")

    headers = {"Authorization": f"Bearer {token}"}

    # 1. Fetch NEW orders
    # We fetch page 1 to find the most recent ones
    try:
        orders_resp = _shiprocket_get(
            "orders", token, params={"page": 1, "per_page": 50}
        )
    except RuntimeError as e:
        logger.error(f"❌ Failed to fetch orders for assignment: {e}")
        raise

    order_list = orders_resp.json().get("data", [])
    new_amazon_orders = []

    for o in order_list:
        status = str(o.get("status", "")).upper()
        channel = str(o.get("channel_name", ""))

        if status in ["NEW", "AWB ASSIGNED", "READY TO SHIP"] and channel == channel_name:
            if target_order_ids and str(o.get("channel_order_id", "")) not in target_order_ids and str(o.get("id", "")) not in target_order_ids:
                continue
            new_amazon_orders.append(o)

    if not new_amazon_orders:
        logger.info(f"✅ No NEW, AWB ASSIGNED, or READY TO SHIP {channel_name} orders found needing assignment/pickup.")
        return True

    logger.info(f"📦 Found {len(new_amazon_orders)} {channel_name} orders needing assignment or pickup. Processing...")

    for o in new_amazon_orders:
        order_id = o.get("id")
        status = str(o.get("status", "")).upper()
        shipments = o.get("shipments", [])
        if not shipments:
            logger.warning(f"⚠️  Order {order_id} has no shipment object. Skipping.")
            continue

        shipment_id = shipments[0].get("id")

        if status in ["AWB ASSIGNED", "READY TO SHIP"]:
            logger.info(f"⚡ Order {order_id} is already assigned (Status: {status}). Scheduling pickup...")
            _schedule_pickup(token, shipment_id, o.get("channel_order_id", order_id), dry_run)
            continue

        logger.debug(f"🔍 Checking serviceability for Order {order_id} (Shipment {shipment_id})...")

        # Get available couriers
        try:
            serv_resp = _shiprocket_get(
                "courier/serviceability/", token,
                params={"order_id": order_id},
                timeout=30
            )
        except RuntimeError as e:
            logger.error(f"❌ Serviceability check failed for {order_id}: {e}")
            continue

        available = serv_resp.json().get("data", {}).get("available_courier_companies", [])

        # Build mapping of available couriers
        available_names = {c.get("courier_name"): c.get("courier_company_id") for c in available}

        # Select best couriers based on preference
        valid_preferences = [p for p in config.COURIER_PREFERENCE if p in available_names]

        if not valid_preferences:
            logger.warning(f"⚠️  None of the preferred couriers are available for {order_id}. Skipping.")
            continue

        awb_assigned = False
        for chosen_name in valid_preferences:
            chosen_id = available_names[chosen_name]
            logger.debug(f"✨ Attempting {chosen_name} (ID: {chosen_id})..." + ("" if not dry_run else " (dry-run)"))

            if dry_run:
                _generate_and_download_label(token, shipment_id, o.get("channel_order_id", order_id), dry_run=True)
                _schedule_pickup(token, shipment_id, o.get("channel_order_id", order_id), dry_run=True)
                awb_assigned = True  # Mock success
                break

            # Assign courier and generate AWB
            try:
                assign_resp = requests.post(
                    f"{API_BASE}/courier/assign/awb",
                    headers=headers,
                    json={
                        "shipment_id": shipment_id,
                        "courier_id": chosen_id
                    },
                    timeout=60
                )
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Assignment request failed for {chosen_name}: {e}")
                continue

            if assign_resp.status_code == 200:
                res_data = assign_resp.json()
                response_obj = res_data.get("response", {})

                # Robust extraction of AWB
                awb = None
                if isinstance(response_obj, dict):
                    data_obj = response_obj.get("data", {})
                    if isinstance(data_obj, dict):
                        awb = data_obj.get("awb_code")

                if awb:
                    logger.info(f"✅ Successfully assigned {chosen_name}. AWB: {awb}")
                    _generate_and_download_label(token, shipment_id, o.get("channel_order_id", order_id))
                    _schedule_pickup(token, shipment_id, o.get("channel_order_id", order_id), dry_run)
                    awb_assigned = True
                    break
                else:
                    # Capture specific error message from Shiprocket response
                    msg = "Unknown error"
                    if isinstance(response_obj, dict):
                        msg = response_obj.get("data", "Unknown format")
                    elif isinstance(response_obj, str):
                        msg = response_obj

                    logger.warning(f"⚠️  {chosen_name} failed: {str(msg)[:200]}. Trying next...")
            else:
                logger.error(f"❌ Assignment failed for {chosen_name} ({_safe_error_snippet(assign_resp)})")

        if not awb_assigned:
            logger.error(f"❌ All preferred couriers failed for {order_id}.")

    return True


def _generate_and_download_label(token: str, shipment_id: int, amazon_order_id: str, dry_run: bool = False) -> str | None:
    """
    Calls Shiprocket API to generate a label and downloads it locally.
    Returns the downloaded file path on success, None on failure.
    In dry-run mode, returns the path that would have been written.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    logger.debug(f"📄 Generating label for Order {amazon_order_id}..." + (" (dry-run)" if dry_run else ""))

    if dry_run:
        date_str = datetime.now().strftime("%d-%m-%Y")
        would_path = os.path.join(LABEL_DIR, date_str, f"{amazon_order_id}.pdf")
        logger.debug(f"📥 Would download label into {would_path} (dry-run)")
        return would_path

    # 1. Generate Label
    payload = {"shipment_id": [shipment_id]}
    try:
        resp = requests.post(
            f"{API_BASE}/courier/generate/label",
            headers=headers,
            json=payload,
            timeout=60
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Label generation request failed: {e}")
        return None

    if resp.status_code != 200:
        logger.error(f"❌ Label generation failed ({_safe_error_snippet(resp)})")
        return None

    label_url = resp.json().get("label_url")
    if not label_url:
        logger.error(f"❌ No label_url in response for {amazon_order_id}")
        return None

    logger.debug(f"📥 Downloading label from: {label_url}")

    # 2. Download File
    try:
        file_resp = requests.get(label_url, timeout=30)
        if file_resp.status_code == 200:
            # Create date-based subdirectory: labels/DD-MM-YYYY
            date_str = datetime.now().strftime("%d-%m-%Y")
            target_folder = os.path.join(LABEL_DIR, date_str)

            if not os.path.exists(target_folder):
                os.makedirs(target_folder)

            file_path = os.path.join(target_folder, f"{amazon_order_id}.pdf")
            with open(file_path, "wb") as f:
                f.write(file_resp.content)
            logger.info(f"✅ Label saved to: {file_path}")
            return file_path
        else:
            logger.error(f"❌ Failed to download label file: {file_resp.status_code}")
            return None
    except Exception as e:
        logger.error(f"❌ Error downloading label: {str(e)}")
        return None


def _schedule_pickup(token: str, shipment_id: int, amazon_order_id: str, dry_run: bool = False):
    """
    Schedules a pickup for an assigned shipment on Shiprocket.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    logger.debug(f"📅 Scheduling pickup for Order {amazon_order_id}..." + (" (dry-run)" if dry_run else ""))

    if dry_run:
        logger.debug(f"✅ Would schedule pickup for shipment {shipment_id} (dry-run)")
        return

    payload = {"shipment_id": [shipment_id]}
    try:
        resp = requests.post(
            f"{API_BASE}/courier/generate/pickup",
            headers=headers,
            json=payload,
            timeout=45
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Pickup scheduling request failed: {e}")
        return

    if resp.status_code == 200:
        logger.info(f"✅ Successfully scheduled pickup for Order {amazon_order_id}.")
    else:
        logger.error(f"❌ Pickup scheduling failed ({_safe_error_snippet(resp)})")


def _get_api_token(email: str, password: str) -> str | None:
    """Internal helper to get JWT token."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    auth_resp = requests.post(
        f"{API_BASE}/auth/login",
        json={"email": email, "password": password},
        headers=headers,
        timeout=config.HTTP_TIMEOUT
    )

    if auth_resp.status_code != 200:
        logger.error(f"❌ API Login failed ({_safe_error_snippet(auth_resp)})")
        return None

    return auth_resp.json().get("token")


def _fetch_orders(token: str) -> List[Dict]:
    """
    Internal helper to fetch all syncable orders.
    Raises RuntimeError on any page-fetch failure so partial data is never
    silently returned as complete.
    """
    orders = []

    order_list = []
    logger.info(
        f"📦 Fetching recent orders from API "
        f"(up to {config.SHIPROCKET_MAX_PAGES * config.SHIPROCKET_PAGE_SIZE})..."
    )

    for page in range(1, config.SHIPROCKET_MAX_PAGES + 1):
        params = {
            "page": page,
            "per_page": config.SHIPROCKET_PAGE_SIZE
        }

        try:
            orders_resp = _shiprocket_get("orders", token, params=params)
        except RuntimeError as e:
            logger.error(f"❌ Failed to fetch orders on page {page}: {e}")
            raise RuntimeError(f"Shiprocket order fetch failed on page {page}") from e

        data = orders_resp.json()
        page_orders = data.get("data", [])
        if not page_orders:
            break

        order_list.extend(page_orders)

    logger.info(f"📋 Retrieved {len(order_list)} total recent orders from API")

    for o in order_list:
        sr_id = str(o.get("id", ""))
        channel_id = str(o.get("channel_order_id", ""))

        # Only process Amazon orders (they typically look like 402-1234567-1234567)
        if not re.search(r'\b(\d{3}-\d{7}-\d{7})\b', channel_id):
            continue

        # Filter: We want any order that hasn't been shipped/delivered/cancelled yet.
        # This includes NEW, READY TO SHIP, PICKUP SCHEDULED, PICKUP GENERATED, AWB ASSIGNED, etc.
        status = str(o.get("status", "")).upper()
        # Broad list of active pre-shipment statuses
        ACTIVE_STATUSES = [
            "NEW", "READY TO SHIP", "PICKUP SCHEDULED", "PICKUP GENERATED",
            "AWB ASSIGNED", "PICKUP RESCHEDULED", "PICKUP ERROR", "MANIFEST GENERATED"
        ]
        if status not in ACTIVE_STATUSES:
            continue

        if o.get("channel_name") != config.AMAZON_CHANNEL_NAME:
            continue

        awb = ""
        courier = ""

        # 1. Try top-level awb_code
        if o.get("awb_code"):
            awb = o.get("awb_code")
            courier = o.get("courier_name")

        # 2. Try shipments array (common for Ready to Ship / Pickup Scheduled orders)
        if not awb:
            shipments = o.get("shipments", [])
            for shipment in shipments:
                if isinstance(shipment, dict) and shipment.get("awb"):
                    awb = shipment.get("awb")
                    # Try 'courier' then 'courier_name'
                    courier = shipment.get("courier") or shipment.get("courier_name")
                    break

        # 3. Try awb_data if still not found
        if not awb and o.get("awb_data"):
            awb_data = o.get("awb_data")
            if isinstance(awb_data, dict):
                awb = awb_data.get("awb_code")
                courier = awb_data.get("courier_name") or awb_data.get("courier")

        if awb:
            # Map couriers to consistent names similar to web scrape
            if courier:
                # Basic normalization
                if "delhivery" in courier.lower():
                    courier = "Delhivery Surface" if "surface" in courier.lower() else "Delhivery"
                elif "blue dart" in courier.lower() or "bluedart" in courier.lower():
                    courier = "Blue Dart Surface" if "surface" in courier.lower() else "Blue Dart"

        orders.append({
            "shiprocket_order_id": sr_id,
            "amazon_order_id": channel_id,
            "courier_name": courier,
            "awb_number": awb,
            "_status": status  # for debug printing
        })

    logger.info(f"✅ Extracted {len(orders)} Amazon orders with AWB data (excluding shipped/delivered)")

    return orders


def check_new_orders(email, password, channel_name: str = config.AMAZON_CHANNEL_NAME):
    """
    Query Shiprocket for any 'NEW' orders from the given channel.
    Does NOT modify anything. Raises on authentication or fetch failure.
    """
    token = _get_api_token(email, password)
    if not token:
        raise RuntimeError("Shiprocket authentication failed")

    logger.info(f"🔍 Checking Shiprocket for NEW {channel_name} orders...")
    # Fetch first 50 orders (usually enough for a quick check)
    resp = _shiprocket_get("orders", token,
                           params={"status": "NEW", "per_page": 50})

    data = resp.json()
    all_orders = data.get("data", [])

    # Filter for the channel
    return [
        o for o in all_orders
        if o.get("channel_name") == channel_name and o.get("status") == "NEW"
    ]


def download_label_for_specific_order(email: str, password: str, shiprocket_order_id: str, amazon_order_id: str, dry_run: bool = False):
    """
    Force downloads label for a specific Shiprocket order ID using the show endpoint.
    Returns True only if a label file was actually downloaded (or would be, in dry-run).
    """
    token = _get_api_token(email, password)
    if not token:
        logger.error("❌ Authentication failed during force download.")
        return False

    logger.info(f"🔍 Fetching shipment details for Shiprocket Order {shiprocket_order_id}...")

    try:
        resp = _shiprocket_get(f"orders/show/{shiprocket_order_id}", token)
    except RuntimeError as e:
        logger.error(f"❌ Failed to fetch order {shiprocket_order_id}: {e}")
        return False

    data = resp.json().get("data", {})
    shipments = data.get("shipments")
    sid = None
    if isinstance(shipments, dict):
        sid = shipments.get("id")
    elif isinstance(shipments, list) and len(shipments) > 0:
        sid = shipments[0].get("id")

    if not sid:
        logger.error(f"❌ No shipment ID found for order {shiprocket_order_id}")
        return False

    label_path = _generate_and_download_label(token, sid, amazon_order_id, dry_run)
    return label_path is not None
