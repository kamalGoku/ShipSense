"""
amazon_api.py – Amazon Selling Partner API (SP-API) integration.
Provides methods to confirm shipments using the official Amazon API.
"""

import os
import time
import requests
from datetime import datetime, timezone
from logger import get_logger

logger = get_logger(__name__)

# ── SP-API Configuration ──────────────────────────────────────────────
AMAZON_CLIENT_ID     = os.getenv("AMAZON_CLIENT_ID", "")
AMAZON_CLIENT_SECRET = os.getenv("AMAZON_CLIENT_SECRET", "")
AMAZON_REFRESH_TOKEN = os.getenv("AMAZON_REFRESH_TOKEN", "")
AMAZON_MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "A21TJRUUN4KGV") # Default India

# SP-API Endpoints
LWA_ENDPOINT = "https://api.amazon.com/auth/o2/token"
SP_API_BASE  = "https://sellingpartnerapi-eu.amazon.com" # India uses EU endpoint (eu-west-1)

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
    
    response = requests.post(LWA_ENDPOINT, data=payload)
    response.raise_for_status()
    
    data = response.json()
    _access_token = data["access_token"]
    _token_expiry = time.time() + int(data["expires_in"])
    
    return _access_token


def get_order_items(amazon_order_id: str) -> list:
    """
    Fetch order item IDs for a given Amazon Order ID.
    Required because confirmShipment needs the specific orderItemId.
    """
    try:
        token = get_lwa_access_token()
        url = f"{SP_API_BASE}/orders/v0/orders/{amazon_order_id}/orderItems"
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
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
    For India marketplace, specific codes are required for known couriers.
    """
    name = courier_name.lower()
    
    if "delhivery" in name:
        return "Delhivery", "Delhivery", courier_name
    if "blue dart" in name or "bluedart" in name:
        # User specified no space and custom service mapping
        return "BlueDart", "BlueDart", courier_name
    if "ecom express" in name or "ecom" in name:
        return "Ecom Express", "Ecom Express", courier_name
    if "xpressbees" in name:
        return "Xpressbees", "Xpressbees", courier_name
    if "ekart" in name:
        return "Ekart", "Ekart", courier_name
    
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
    Returns "SUCCESS" or an error message.
    """
    if dry_run:
        logger.info(f"[DRY RUN] Would confirm shipment for {amazon_order_id} via API")
        return "SUCCESS"

    market_id = os.getenv("AMAZON_MARKETPLACE_ID", "A21TJRUUN4KGV")
    
    # Map courier to Amazon-specific codes
    carrier_code, amazon_carrier_name, shipping_method = map_carrier_to_spapi(courier_name)

    try:
        token = get_lwa_access_token()
        
        # 1. Fetch Order Items (MANDATORY for confirmShipment)
        order_items = get_order_items(amazon_order_id)
        if not order_items:
            return "ERROR: Could not fetch order items from Amazon"
            
        # Endpoint for confirmShipment (Orders V0)
        url = f"{SP_API_BASE}/orders/v0/orders/{amazon_order_id}/shipmentConfirmation"
        
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "x-amz-date": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
        }
        
        # Payload for shipment confirmation
        payload = {
            "marketplaceId": market_id,
            "packageDetail": {
                "packageReferenceId": "1", # Arbitrary
                "carrierCode": carrier_code,
                "carrierName": amazon_carrier_name,
                "shippingMethod": shipping_method,
                "trackingNumber": awb_number,
                "shipDate": datetime.now(timezone.utc).isoformat(),
                "orderItems": order_items    # MANDATORY: List of {orderItemId, quantity}
            }
        }
        
        logger.info(f"📡 Confirming shipment for {amazon_order_id} via SP-API...")
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 204:
            return "SUCCESS"
        else:
            try:
                err_data = response.json()
                error_msg = err_data.get("errors", [{}])[0].get("message", "Unknown API Error")
                return f"ERROR: {error_msg}"
            except:
                return f"ERROR: Status {response.status_code} - {response.text}"

    except Exception as e:
        return f"ERROR: {str(e)}"


def fetch_amazon_new_orders() -> list[dict]:
    """
    Fetch 'Unshipped' and 'PartiallyShipped' orders from Amazon SP-API.
    Returns a list of standardized order dictionaries.
    """
    market_id = os.getenv("AMAZON_MARKETPLACE_ID", "A21TJRUUN4KGV")
    
    try:
        token = get_lwa_access_token()
        
        # Endpoint for listing orders
        url = f"{SP_API_BASE}/orders/v0/orders"
        
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
        }
        
        # Fetch orders from the last 7 days
        # This helps ensure we catch everything pending without too much noise
        from datetime import timedelta
        created_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        params = {
            "MarketplaceIds": market_id,
            "OrderStatuses": "Unshipped,PartiallyShipped",
            "CreatedAfter": created_after
        }
        
        logger.info("📡 Fetching unshipped orders from Amazon SP-API...")
        response = requests.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        orders = data.get("payload", {}).get("Orders", [])
        
        result = []
        for o in orders:
            result.append({
                "amazon_order_id": o.get("AmazonOrderId"),
                "status": o.get("OrderStatus"),
                "created_at": o.get("PurchaseDate"),
                "total": o.get("OrderTotal", {}).get("Amount", "0.00"),
                "items_unshipped": int(o.get("NumberOfItemsUnshipped", 0))
            })
        return result

    except Exception as e:
        logger.error(f"❌ Failed to fetch Amazon orders: {e}")
        return []

def fetch_amazon_all_orders(days_back: int = 1825, last_updated_after: str = None) -> list[dict]:
    """
    Fetch 'Shipped' and 'Canceled' orders from Amazon SP-API.
    """
    market_id = os.getenv("AMAZON_MARKETPLACE_ID", "A21TJRUUN4KGV")
    
    try:
        token = get_lwa_access_token()
        url = f"{SP_API_BASE}/orders/v0/orders"
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
        }
        
        from datetime import timedelta
        
        params = {
            "MarketplaceIds": market_id,
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
        
        result = []
        next_token = None
        
        while True:
            if next_token:
                req_params = {"NextToken": next_token}
            else:
                req_params = params
                
            response = requests.get(url, headers=headers, params=req_params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            payload = data.get("payload", {})
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
        return []

def get_order_items_details(amazon_order_id: str) -> list[dict]:
    """
    Fetch order items to get SKU and Sale Price.
    """
    try:
        token = get_lwa_access_token()
        url = f"{SP_API_BASE}/orders/v0/orders/{amazon_order_id}/orderItems"
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
        }
        
        # Retry logic for 429 rate limit
        retries = 3
        backoff = 3
        for attempt in range(retries):
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429:
                logger.warning(f"⚠️ Rate limited (429) on orderItems for {amazon_order_id}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
                continue
            response.raise_for_status()
            break
        
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
    Returns a dict with total fees, refunds, etc.
    """
    try:
        token = get_lwa_access_token()
        url = f"{SP_API_BASE}/finances/v0/orders/{amazon_order_id}/financialEvents"
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "User-Agent": "ShiprocketAgent/1.0 (Language=Python)"
        }
        
        # Retry logic for 429 rate limit
        retries = 3
        backoff = 3
        for attempt in range(retries):
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 429:
                logger.warning(f"⚠️ Rate limited (429) on finances for {amazon_order_id}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
                continue
            response.raise_for_status()
            break
            
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
                    total_amazon_fees += -fee_amount # Add to total fees (positive value)
                    
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
            "total_amazon_fees": total_amazon_fees,
            "total_refunds": total_refunds
        }
    except Exception as e:
        # 404 typically means no financial events yet
        if hasattr(e, "response") and e.response is not None and e.response.status_code == 404:
            return {"total_amazon_fees": 0.0, "total_refunds": 0.0}
        logger.error(f"❌ Failed to fetch finances for {amazon_order_id}: {e}")
        return {"total_amazon_fees": 0.0, "total_refunds": 0.0}

