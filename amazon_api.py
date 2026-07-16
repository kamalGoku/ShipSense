"""
amazon_api.py – Amazon Selling Partner API (SP-API) integration.
Provides methods to confirm shipments using the official Amazon API.
"""

import os
import time
import requests
from datetime import datetime, timezone, timedelta

import config
from logger import get_logger

logger = get_logger(__name__)

# LWA token endpoint (not part of SP-API base URL)
LWA_ENDPOINT = "https://api.amazon.com/auth/o2/token"

# Global for token caching
_access_token = None
_token_expiry = 0


def get_lwa_access_token():
    """Get an LWA access token using the refresh token."""
    global _access_token, _token_expiry

    # Reload config inside to ensure it picks up load_dotenv() from main
    client_id = os.getenv("AMAZON_CLIENT_ID", "")
    client_secret = os.getenv("AMAZON_CLIENT_SECRET", "")
    refresh_token = os.getenv("AMAZON_REFRESH_TOKEN", "")

    # Check if existing token is still valid (with 5 min buffer)
    if _access_token and time.time() < (_token_expiry - 300):
        return _access_token

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError("Missing Amazon SP-API credentials in .env")

    logger.info("🔑 Refreshing Amazon LWA Access Token...")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }

    response = requests.post(LWA_ENDPOINT, data=payload, timeout=config.HTTP_TIMEOUT)
    response.raise_for_status()

    data = response.json()
    _access_token = data["access_token"]
    _token_expiry = time.time() + int(data["expires_in"])

    return _access_token


def _spapi_headers(token: str) -> dict:
    """Standard headers for SP-API requests."""
    return {
        "x-amz-access-token": token,
        "Content-Type": "application/json",
        "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
    }


def _spapi_get(url: str, params: dict | None = None,
               retries: int | None = None,
               backoff_seconds: float | None = None) -> requests.Response:
    """
    Perform a GET against SP-API with auth headers, timeout, and 429
    retry/backoff. Raises requests.HTTPError on any non-2xx final response
    (including 429 after retry exhaustion). Never logs response bodies.
    """
    token = get_lwa_access_token()
    headers = _spapi_headers(token)

    retries = config.RATE_LIMIT_RETRIES if retries is None else retries
    backoff = (config.RATE_LIMIT_BACKOFF_SECONDS
               if backoff_seconds is None else backoff_seconds)

    response = None
    for attempt in range(max(retries, 1)):
        response = requests.get(url, headers=headers, params=params,
                                timeout=config.HTTP_TIMEOUT)
        if response.status_code != 429:
            break
        if attempt < max(retries, 1) - 1:
            logger.warning(
                f"⚠️ Rate limited (429) on SP-API GET "
                f"(attempt {attempt + 1}/{retries}). Retrying in {backoff}s..."
            )
            time.sleep(backoff)
            backoff *= 2

    # Raises on final 429 (retry exhaustion) or any other HTTP error.
    response.raise_for_status()
    return response


def get_order_items(amazon_order_id: str) -> list:
    """
    Fetch order item IDs for a given Amazon Order ID.
    Required because confirmShipment needs the specific orderItemId.
    Returns [] on failure.
    """
    try:
        url = f"{config.SPAPI_BASE_URL}/orders/v0/orders/{amazon_order_id}/orderItems"
        response = _spapi_get(url)

        data = response.json()
        # SP-API casing is usually PascalCase for the keys in this response
        items = data.get("payload", {}).get("OrderItems", [])

        # We need to return a list of {orderItemId: ..., quantity: ...}
        result = []
        for item in items:
            result.append({
                "orderItemId": item.get("OrderItemId"),
                "quantity": int(item.get("QuantityOrdered", 1))
            })
        return result
    except Exception as e:
        logger.error(f"❌ Failed to fetch order items for {amazon_order_id}: {e}")
        return []


def map_carrier_to_spapi(courier_name: str) -> tuple[str, str, str]:
    """
    Maps Shiprocket courier name to Amazon SP-API:
    (carrierCode, carrierName, shippingMethod)
    Driven by config.CARRIER_SPAPI_MAP (case-insensitive substring match,
    first match wins). shippingMethod is the original courier name for
    known carriers.
    """
    name = courier_name.lower()

    for substring, (carrier_code, carrier_name) in config.CARRIER_SPAPI_MAP:
        if substring in name:
            return carrier_code, carrier_name, courier_name

    # Fallback to Others
    return "Others", courier_name, "Standard"


def confirm_shipment_api(
    amazon_order_id: str,
    courier_name: str,
    awb_number: str,
    dry_run: bool = False
) -> str:
    """
    Confirm shipment of an order via SP-API.
    Returns "SUCCESS" or an error message string starting with "ERROR:".
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would confirm shipment for {amazon_order_id} via API")
        return "SUCCESS"

    # Map courier to Amazon-specific codes
    carrier_code, amazon_carrier_name, shipping_method = map_carrier_to_spapi(courier_name)

    try:
        token = get_lwa_access_token()

        # 1. Fetch Order Items (MANDATORY for confirmShipment)
        order_items = get_order_items(amazon_order_id)
        if not order_items:
            return "ERROR: Could not fetch order items from Amazon"

        # Endpoint for confirmShipment (Orders V0)
        url = f"{config.SPAPI_BASE_URL}/orders/v0/orders/{amazon_order_id}/shipmentConfirmation"

        headers = _spapi_headers(token)
        headers["x-amz-date"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        # Payload for shipment confirmation
        payload = {
            "marketplaceId": config.AMAZON_MARKETPLACE_ID,
            "packageDetail": {
                "packageReferenceId": "1",  # Arbitrary
                "carrierCode": carrier_code,
                "carrierName": amazon_carrier_name,
                "shippingMethod": shipping_method,
                "trackingNumber": awb_number,
                "shipDate": datetime.now(timezone.utc).isoformat(),
                "orderItems": order_items    # MANDATORY: List of {orderItemId, quantity}
            }
        }

        logger.info(f"📡 Confirming shipment for {amazon_order_id} via SP-API...")

        # POST with 429 retry/backoff
        response = None
        backoff = config.RATE_LIMIT_BACKOFF_SECONDS
        for attempt in range(max(config.RATE_LIMIT_RETRIES, 1)):
            response = requests.post(url, headers=headers, json=payload,
                                     timeout=config.HTTP_TIMEOUT)
            if response.status_code != 429:
                break
            if attempt < max(config.RATE_LIMIT_RETRIES, 1) - 1:
                logger.warning(
                    f"⚠️ Rate limited (429) confirming shipment for "
                    f"{amazon_order_id}. Retrying in {backoff}s..."
                )
                time.sleep(backoff)
                backoff *= 2

        if response.status_code == 204:
            return "SUCCESS"
        else:
            try:
                err_data = response.json()
                error_msg = err_data.get("errors", [{}])[0].get("message", "Unknown API Error")
                return f"ERROR: {str(error_msg)[:200]}"
            except Exception:
                # Do not include the response body (may contain PII)
                return f"ERROR: Status {response.status_code}"

    except Exception as e:
        return f"ERROR: {str(e)}"


def fetch_amazon_new_orders() -> list[dict]:
    """
    Fetch 'Unshipped' and 'PartiallyShipped' orders from Amazon SP-API,
    following NextToken pagination until all pages are retrieved.
    Returns a list of standardized order dictionaries.

    Raises on failure (auth errors, network errors, rate-limit exhaustion):
    partial pagination results are never returned as if complete.
    """
    url = f"{config.SPAPI_BASE_URL}/orders/v0/orders"

    # Fetch orders from the last 7 days
    # This helps ensure we catch everything pending without too much noise
    created_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "MarketplaceIds": config.AMAZON_MARKETPLACE_ID,
        "OrderStatuses": "Unshipped,PartiallyShipped",
        "CreatedAfter": created_after
    }

    try:
        logger.info("📡 Fetching unshipped orders from Amazon SP-API...")

        result = []
        next_token = None

        while True:
            if next_token:
                req_params = {"NextToken": next_token}
            else:
                req_params = params

            response = _spapi_get(url, params=req_params)

            payload = response.json().get("payload", {})
            orders = payload.get("Orders", [])

            for o in orders:
                result.append({
                    "amazon_order_id": o.get("AmazonOrderId"),
                    "status": o.get("OrderStatus"),
                    "created_at": o.get("PurchaseDate"),
                    "total": o.get("OrderTotal", {}).get("Amount", "0.00"),
                    "items_unshipped": int(o.get("NumberOfItemsUnshipped", 0))
                })

            next_token = payload.get("NextToken")
            if not next_token:
                break

            time.sleep(1)

        return result

    except Exception as e:
        logger.error(f"❌ Failed to fetch Amazon unshipped orders: {e}")
        raise


def fetch_amazon_all_orders(days_back: int = config.INITIAL_SYNC_DAYS,
                            last_updated_after: str = None) -> list[dict]:
    """
    Fetch 'Shipped' and 'Canceled' orders from Amazon SP-API, following
    NextToken pagination until all pages are retrieved.

    Raises on failure (auth errors, network errors, rate-limit exhaustion):
    partial pagination results are never returned as if complete.
    """
    url = f"{config.SPAPI_BASE_URL}/orders/v0/orders"

    params = {
        "MarketplaceIds": config.AMAZON_MARKETPLACE_ID,
        "OrderStatuses": "Shipped,Canceled",
        "MaxResultsPerPage": 100
    }

    if last_updated_after:
        params["LastUpdatedAfter"] = last_updated_after
        logger.info(f"📡 Fetching Shipped/Canceled orders from Amazon SP-API updated after {last_updated_after}...")
    else:
        created_after = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params["CreatedAfter"] = created_after
        logger.info(f"📡 Fetching Shipped/Canceled orders from Amazon SP-API created after {created_after}...")

    try:
        result = []
        next_token = None

        while True:
            if next_token:
                req_params = {"NextToken": next_token}
            else:
                req_params = params

            response = _spapi_get(url, params=req_params)

            payload = response.json().get("payload", {})
            orders = payload.get("Orders", [])

            for o in orders:
                result.append({
                    "amazon_order_id": o.get("AmazonOrderId"),
                    "status": o.get("OrderStatus"),
                    "created_at": o.get("PurchaseDate"),
                    "updated_at": o.get("LastUpdateDate"),
                    "total": float(o.get("OrderTotal", {}).get("Amount", "0.00")),
                    "currency": o.get("OrderTotal", {}).get("CurrencyCode", "INR")
                })

            next_token = payload.get("NextToken")
            if not next_token:
                break

            time.sleep(1)

        return result

    except Exception as e:
        logger.error(f"❌ Failed to fetch all Amazon orders: {e}")
        raise


def get_order_items_details(amazon_order_id: str) -> list[dict]:
    """
    Fetch order items to get SKU and Sale Price.
    Returns [] on failure.
    """
    try:
        url = f"{config.SPAPI_BASE_URL}/orders/v0/orders/{amazon_order_id}/orderItems"
        response = _spapi_get(url)

        data = response.json()
        items = data.get("payload", {}).get("OrderItems", [])

        result = []
        for item in items:
            price = item.get("ItemPrice", {}).get("Amount", "0.00")
            result.append({
                "sku": item.get("SellerSKU"),
                "price": float(price) if price else 0.0,
                "quantity": int(item.get("QuantityOrdered", 1)),
                "title": item.get("Title")
            })
        return result
    except Exception as e:
        logger.error(f"❌ Failed to fetch order items details for {amazon_order_id}: {e}")
        return []


def fetch_order_finances(amazon_order_id: str) -> dict:
    """
    Fetch financial events for an order to extract exact Amazon fees.

    Returns {"fees": float, "refunds": float, "fetched": True} on success.
    A 404 (no financial events posted yet) returns real zeros with
    "fetched": True. Any other failure RAISES so callers can distinguish
    real zero fees from unfetched data.
    """
    url = f"{config.SPAPI_BASE_URL}/finances/v0/orders/{amazon_order_id}/financialEvents"

    try:
        response = _spapi_get(url)

        data = response.json()
        events = data.get("payload", {}).get("FinancialEvents", {})

        total_amazon_fees = 0.0
        total_refunds = 0.0

        # Parse ShipmentEvents for sale fees
        shipment_events = events.get("ShipmentEventList", [])
        for event in shipment_events:
            for item in event.get("ShipmentItemList", []):
                for fee in item.get("ItemFeeList", []):
                    # Amazon fees are usually negative (charge to seller)
                    fee_amount = float(fee.get("FeeAmount", {}).get("CurrencyAmount", 0.0))
                    total_amazon_fees += -fee_amount  # Add to total fees (positive value)

        # Parse RefundEvents for refund fees and principal refunds
        refund_events = events.get("RefundEventList", [])
        for event in refund_events:
            # 1. Look in ShipmentItemAdjustmentList (for corrections/adjustments)
            for item in event.get("ShipmentItemAdjustmentList", []):
                # Handle fee adjustments (e.g. refunded commission, refund admin fee)
                for fee in item.get("ItemFeeAdjustmentList", []):
                    fee_amount = float(fee.get("FeeAmount", {}).get("CurrencyAmount", 0.0))
                    total_amazon_fees += -fee_amount

                # Handle principal refunds (ignore tax and TCS for tax-exclusive P&L)
                for price_adj in item.get("ItemChargeAdjustmentList", []):
                    charge_type = price_adj.get("ChargeType", "")
                    if charge_type == "Principal":
                        adj_amount = float(price_adj.get("ChargeAmount", {}).get("CurrencyAmount", 0.0))
                        total_refunds += abs(adj_amount)

            # 2. Look in ShipmentItemList (standard refund flow)
            for item in event.get("ShipmentItemList", []):
                for fee in item.get("ItemFeeList", []):
                    fee_amount = float(fee.get("FeeAmount", {}).get("CurrencyAmount", 0.0))
                    total_amazon_fees += -fee_amount

                for charge in item.get("ItemChargeList", []):
                    charge_type = charge.get("ChargeType", "")
                    if charge_type == "Principal":
                        adj_amount = float(charge.get("ChargeAmount", {}).get("CurrencyAmount", 0.0))
                        total_refunds += abs(adj_amount)

        return {
            "fees": total_amazon_fees,
            "refunds": total_refunds,
            "fetched": True
        }
    except Exception as e:
        # 404 typically means no financial events posted yet: real zeros.
        if (isinstance(e, requests.HTTPError)
                and e.response is not None
                and e.response.status_code == 404):
            return {"fees": 0.0, "refunds": 0.0, "fetched": True}
        # Log status code only, never the response body (PII).
        status = ""
        if isinstance(e, requests.HTTPError) and e.response is not None:
            status = f" (status {e.response.status_code})"
        logger.error(f"❌ Failed to fetch finances for {amazon_order_id}{status}: {type(e).__name__}")
        raise
