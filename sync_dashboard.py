import json
import csv
import os
import time
from datetime import datetime, timezone
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
from amazon_api import fetch_amazon_all_orders, get_order_items_details, fetch_order_finances
from logger import get_logger
from freight_db import FreightDB
from order_db import OrderDB

logger = get_logger(__name__)

# Load environment variables
load_dotenv()

DATA_FILE = "server/dashboard_data.json"
COSTS_FILE = "product_costs.csv"
FREIGHT_DIR = "freight"
STATE_FILE = "sync_state.json"
SHIPROCKET_API_BASE = "https://apiv2.shiprocket.in/v1/external"

def load_product_costs():
    costs = {}
    if os.path.exists(COSTS_FILE):
        with open(COSTS_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                sku = row.get('sku', '').strip()
                try:
                    cost = float(row.get('cost_price', 0))
                    if sku:
                        costs[sku] = cost
                except ValueError:
                    pass
    return costs

def load_freight_costs():
    db = FreightDB()
    freight = db.get_all_freight_costs()
    return freight

def load_sync_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                pass
    return {}

def fetch_api_freight_costs(sync_state, db_freight):
    """
    Fetch actual billed freight costs from Shiprocket API for orders
    not covered by the SQLite DB. Uses the orders/show endpoint or search
    endpoint and inserts results into the DB.
    """
    email = os.getenv('SHIPROCKET_EMAIL')
    password = os.getenv('SHIPROCKET_PASSWORD')
    if not email or not password:
        logger.warning("⚠️ Shiprocket credentials missing, skipping API freight fetch")
        return {}

    db = FreightDB()
    
    # Identify orders that need API lookup
    orders_needing_freight = []
    for entry in sync_state.get("orders", []):
        sr_id = entry.get("shiprocket_order_id")
        amz_id = str(entry.get("amazon_order_id", ""))
        awb = entry.get("awb_number")

        if not sr_id:
            continue

        # Check if already in our dict from DB
        if amz_id and amz_id in db_freight:
            continue
        if awb and awb in db_freight:
            continue

        orders_needing_freight.append(entry)

    if not orders_needing_freight:
        logger.info("✅ All orders have freight data from DB, no API calls needed.")
        return {}

    logger.info(f"📡 Fetching freight from Shiprocket API for {len(orders_needing_freight)} orders missing DB data...")

    # Authenticate
    try:
        auth_resp = requests.post(
            f"{SHIPROCKET_API_BASE}/auth/login",
            json={"email": email, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        if auth_resp.status_code != 200:
            logger.error(f"❌ Shiprocket auth failed for freight fetch: {auth_resp.status_code}")
            return {}
        token = auth_resp.json().get("token")
    except Exception as e:
        logger.error(f"❌ Shiprocket auth error: {e}")
        return {}

    headers = {"Authorization": f"Bearer {token}"}
    api_freight = {}
    fetched = 0

    for entry in orders_needing_freight:
        sr_id = str(entry["shiprocket_order_id"])
        amz_id = str(entry.get("amazon_order_id", ""))
        awb = entry.get("awb_number", "")
        
        if amz_id == "":
            amz_id = None
        if awb == "":
            awb = None

        
        data = None
        real_sr_id = None
        
        try:
            if sr_id.startswith("LIRIYA_") and amz_id:
                # Search by channel order id
                resp = requests.get(
                    f"{SHIPROCKET_API_BASE}/orders",
                    params={"search": amz_id},
                    headers=headers,
                    timeout=15
                )
                if resp.status_code == 200:
                    results = resp.json().get("data", [])
                    if results:
                        data = results[0]
                        real_sr_id = str(data.get("id"))
            elif sr_id.isdigit():
                real_sr_id = sr_id
                resp = requests.get(
                    f"{SHIPROCKET_API_BASE}/orders/show/{sr_id}",
                    headers=headers,
                    timeout=15
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})

            if data:
                awb_data = data.get("awb_data") or {}
                charges = awb_data.get("charges") or {}
                freight_val = charges.get("freight_charges") or charges.get("billing_amount")
                zone = charges.get("zone")
                cw = awb_data.get("charged_weight")

                if freight_val:
                    cost = float(freight_val)
                else:
                    status_str = str(data.get("status", "")).upper()
                    status_code = data.get("status_code")
                    if status_str == "CANCELED" or status_code == 5:
                        cost = 0.0
                    else:
                        cost = None
                        
                if cost is not None:
                    # Insert to DB permanently
                    db.insert_freight(
                        channel_order_id=amz_id,
                        shiprocket_order_id=real_sr_id,
                        awb_number=awb,
                        freight_amount=cost,
                        charged_weight=cw,
                        zone=zone,
                        source='api'
                    )
                    # Add to our local dict for this run
                    if amz_id:
                        api_freight[amz_id] = cost
                    if awb:
                        api_freight[awb] = cost
                    fetched += 1
                    logger.debug(f"  💰 SR#{real_sr_id} ({amz_id}) → ₹{cost}")
            else:
                logger.debug(f"  ⚠️ Could not fetch data for SR#{sr_id} / AMZ#{amz_id}")
        except Exception as e:
            logger.debug(f"  ⚠️ Error for SR#{sr_id}: {e}")

        time.sleep(0.3)  # Gentle rate limiting

    logger.info(f"✅ Fetched freight for {fetched}/{len(orders_needing_freight)} orders from Shiprocket API.")
    return api_freight

def fetch_woocommerce_orders(days_back: int = 1825, modified_after: str = None) -> list:
    """Fetch orders from WooCommerce REST API."""
    url = f"{os.getenv('WOOCOMMERCE_STORE_URL')}/wp-json/wc/v3/orders"
    ck = os.getenv('WOOCOMMERCE_CONSUMER_KEY')
    cs = os.getenv('WOOCOMMERCE_CONSUMER_SECRET')
    if not url or not ck or not cs:
        logger.warning("⚠️ WooCommerce credentials missing in .env")
        return []
        
    try:
        from datetime import timedelta
        
        params = {
            "per_page": 100
        }
        
        if modified_after:
            params["modified_after"] = modified_after
            logger.info(f"📡 Fetching WooCommerce orders modified after {modified_after}...")
        else:
            after_date = (datetime.now() - timedelta(days=days_back)).isoformat()
            params["after"] = after_date
            logger.info("📡 Fetching WooCommerce orders...")
        
        orders = []
        page = 1
        while True:
            params["page"] = page
            response = requests.get(url, auth=HTTPBasicAuth(ck, cs), params=params.copy(), timeout=15)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            orders.extend(batch)
            page += 1
            if len(batch) < 100:
                break
                
        logger.info(f"📦 Fetched {len(orders)} WooCommerce orders.")
        return orders
    except Exception as e:
        logger.error(f"❌ Failed to fetch WooCommerce orders: {e}")
        return []

def sync_data():
    logger.info("🚀 Starting Combined Dashboard Sync...")
    
    product_costs = load_product_costs()
    freight_costs = load_freight_costs()
    sync_state = load_sync_state()
    
    # Augment freight data with Shiprocket API for orders not in CSV
    api_freight = fetch_api_freight_costs(sync_state, freight_costs)
    freight_costs.update(api_freight)
    logger.info(f"📊 Total freight entries: {len(freight_costs)} (CSV + API)")
    
    
    # Platform tracking metrics
    platform_breakdown = {
        "Amazon (LIRIYA)": {"orders": 0, "shipped": 0, "cancelled": 0, "revenue": 0.0, "fees": 0.0, "shipping_cost": 0.0, "profit": 0.0, "cancellation_fees": 0.0},
        "LIRIYA": {"orders": 0, "shipped": 0, "cancelled": 0, "revenue": 0.0, "fees": 0.0, "shipping_cost": 0.0, "profit": 0.0, "cancellation_fees": 0.0}
    }
    
    monthly_data = {}
    products_data = {}
    orders_data = []

    order_db = OrderDB()
    last_synced_amazon = order_db.get_last_synced_timestamp("amazon")
    
    if last_synced_amazon:
        amazon_orders_delta = fetch_amazon_all_orders(last_updated_after=last_synced_amazon)
    else:
        amazon_orders_delta = fetch_amazon_all_orders(days_back=1825)
        
    logger.info(f"📦 Found {len(amazon_orders_delta)} Amazon orders to update.")
    
    for order in amazon_orders_delta:
        amz_id = order['amazon_order_id']
        status = order['status']
        created_at = order['created_at']
        updated_at = order.get('updated_at', created_at)
        
        existing_order = order_db.get_order(amz_id)
        
        items = existing_order['items'] if existing_order else None
        if not items:
            items = get_order_items_details(amz_id)
            if not items:
                continue
                
        is_cancelled = status == 'Canceled'
        finances = existing_order['finances'] if existing_order else None
        
        # Fetch finances if shipped and not previously fetched (or previously fetched but fees were 0)
        if not is_cancelled and (not finances or finances.get('total_amazon_fees', 0.0) == 0.0):
            time.sleep(2) # 0.5 req/s limit
            finances = fetch_order_finances(amz_id)
            
        order_db.upsert_order(
            order_id=amz_id,
            platform="Amazon (LIRIYA)",
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            items=items,
            finances=finances
        )
        
    # Update timestamp
    new_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    order_db.set_last_synced_timestamp(new_timestamp, "amazon")
    
    # Process from cache
    all_cached_orders = order_db.get_all_orders()
    amazon_cached = [o for o in all_cached_orders if o['platform'] == 'Amazon (LIRIYA)']
    
    for order in amazon_cached:
        amz_id = order['order_id']
        status = order['status']
        created_at = order['created_at']
        month_key = created_at[:7] # YYYY-MM
        
        platform_breakdown["Amazon (LIRIYA)"]["orders"] += 1
        is_shipped = status == 'Shipped'
        is_cancelled = status == 'Canceled'
        
        if is_shipped:
            platform_breakdown["Amazon (LIRIYA)"]["shipped"] += 1
        elif is_cancelled:
            platform_breakdown["Amazon (LIRIYA)"]["cancelled"] += 1
            
        items = order['items']
        if not items:
            continue
            
        primary_sku = items[0]['sku'] if items else "UNKNOWN"
        order_sale_price = sum(i['price'] for i in items)
        order_cost = sum(product_costs.get(i['sku'], 0.0) * i['quantity'] for i in items)
        
        order_fees = 0.0
        order_refunds = 0.0
        
        if not is_cancelled:
            finances = order['finances'] or {}
            order_fees = finances.get('total_amazon_fees', 0.0)
            order_refunds = finances.get('total_refunds', 0.0)
            if order_fees == 0.0 and order_sale_price > 0.0:
                order_fees = round(order_sale_price * 0.25, 2)

            
        ship_cost = 0.0
        if amz_id in freight_costs:
            ship_cost = freight_costs[amz_id]
        else:
            awb = None
            for entry in sync_state.get("orders", []):
                if entry.get("amazon_order_id") == amz_id:
                    awb = entry.get("awb_number")
                    break
            if awb and awb in freight_costs:
                ship_cost = freight_costs[awb]
                
        if is_cancelled:
            order_sale_price = 0.0
            order_cost = 0.0
            order_fees = 0.0
            ship_cost = 0.0
            profit = 0.0
        else:
            # If the order is fully refunded, the inventory is returned to the shelf (or never left).
            # Therefore, we recover the COGS (set it to 0). 
            # A partial refund implies a concession where the customer kept the item, so COGS remains.
            if order_refunds > 0 and order_refunds >= (order_sale_price - 1.0):
                order_cost = 0.0
            
            profit = order_sale_price - order_cost - order_fees - ship_cost - order_refunds
        
        # Accumulate Amazon breakdown
        platform_breakdown["Amazon (LIRIYA)"]["revenue"] += order_sale_price
        platform_breakdown["Amazon (LIRIYA)"]["fees"] += order_fees
        platform_breakdown["Amazon (LIRIYA)"]["shipping_cost"] += ship_cost
        platform_breakdown["Amazon (LIRIYA)"]["profit"] += profit
        
        if order_refunds > 0 and order_refunds >= (order_sale_price - 1.0):
            platform_breakdown["Amazon (LIRIYA)"]["cancellation_fees"] += order_fees
        
        # Monthly aggregates
        if month_key not in monthly_data:
            monthly_data[month_key] = {"month": month_key, "revenue": 0, "profit": 0, "shipped": 0, "cancelled": 0, "shipping_cost": 0}
        monthly_data[month_key]['revenue'] += order_sale_price
        monthly_data[month_key]['profit'] += profit
        monthly_data[month_key]['shipping_cost'] += ship_cost
        if is_shipped: monthly_data[month_key]['shipped'] += 1
        if is_cancelled: monthly_data[month_key]['cancelled'] += 1
        
        # Product aggregates
        for item in items:
            sku = item.get('sku') or "UNKNOWN"
            if sku not in products_data:
                products_data[sku] = {"sku": sku, "name": item['title'], "units_sold": 0, "revenue": 0, "profit": 0}
            products_data[sku]['units_sold'] += item['quantity']
            products_data[sku]['revenue'] += item['price']
            item_cost = product_costs.get(sku, 0.0) * item['quantity']
            num_items = len(items) if len(items) > 0 else 1
            item_profit = item['price'] - item_cost - (order_fees / num_items) - (ship_cost / num_items) if not is_cancelled else 0
            products_data[sku]['profit'] += item_profit
            
        orders_data.append({
            "amazon_order_id": amz_id,
            "platform": "Amazon (LIRIYA)",
            "date": created_at,
            "status": status,
            "sku": primary_sku,
            "sale_price": order_sale_price,
            "product_cost": order_cost,
            "amazon_fees": order_fees,
            "shipping_cost": ship_cost,
            "refunds": order_refunds,
            "profit": profit
        })

    # 2. Process WooCommerce Orders
    last_synced_woo = order_db.get_last_synced_timestamp("woocommerce")
    if last_synced_woo:
        woo_orders_delta = fetch_woocommerce_orders(modified_after=last_synced_woo)
    else:
        woo_orders_delta = fetch_woocommerce_orders(days_back=1825)
        
    logger.info(f"📦 Found {len(woo_orders_delta)} WooCommerce orders to update.")
    
    for woo_order in woo_orders_delta:
        woo_id = str(woo_order.get("id"))
        status = woo_order.get("status")
        created_at = woo_order.get("date_created") # ISO string
        updated_at = woo_order.get("date_modified") or created_at
        
        # Standardize WooCommerce statuses
        if status in ['completed', 'processing']:
            std_status = 'Shipped'
        elif status in ['cancelled', 'refunded', 'failed']:
            std_status = 'Canceled'
        else:
            std_status = 'Pending'
            
        # Get items
        line_items = woo_order.get("line_items", [])
        
        order_db.upsert_order(
            order_id=woo_id,
            platform="LIRIYA",
            status=std_status,
            created_at=created_at,
            updated_at=updated_at,
            items=line_items,
            finances=None # We don't have separate finances API for Woo, just use order total
        )
        
    # Update timestamp
    new_woo_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    order_db.set_last_synced_timestamp(new_woo_timestamp, "woocommerce")
    
    # Process from cache
    woo_cached = [o for o in all_cached_orders if o['platform'] == 'LIRIYA']
    
    for order in woo_cached:
        woo_id = order['order_id']
        std_status = order['status']
        created_at = order['created_at']
        month_key = created_at[:7] # YYYY-MM
        
        platform_breakdown["LIRIYA"]["orders"] += 1
        
        # Determine tracking
        is_shipped = std_status == 'Shipped'
        is_cancelled = std_status == 'Canceled'
        if is_shipped:
            platform_breakdown["LIRIYA"]["shipped"] += 1
        elif is_cancelled:
            platform_breakdown["LIRIYA"]["cancelled"] += 1
            
        line_items = order['items'] or []
        primary_sku = line_items[0].get("sku") if line_items else "UNKNOWN"
        
        # Calculate sale price
        # Wait, the items in DB for woo have price, quantity, etc.
        # But for total we might have just used woo_order.get("total").
        # If we didn't save the total, we should calculate from items or save finances={"total": ...}
        # Actually, let's just calculate from items for consistency.
        # Wait, woo items have 'total' field as string.
        order_sale_price = sum(float(i.get('total', 0.0)) for i in line_items)
        order_cost = sum(product_costs.get(i.get("sku"), 0.0) * int(i.get("quantity", 1)) for i in line_items)
        
        # Standard 2% Payment Gateway Fee for WooCommerce payments
        order_fees = round(order_sale_price * 0.02, 2) if not is_cancelled else 0.0
        
        ship_cost = 0.0
        if woo_id in freight_costs:
            ship_cost = freight_costs[woo_id]
        elif f"LIRIYA_{woo_id}" in freight_costs:
            ship_cost = freight_costs[f"LIRIYA_{woo_id}"]
        else:
            awb = None
            for entry in sync_state.get("orders", []):
                if entry.get("amazon_order_id") == woo_id or entry.get("amazon_order_id") == f"LIRIYA_{woo_id}":
                    awb = entry.get("awb_number")
                    break
            if awb and awb in freight_costs:
                ship_cost = freight_costs[awb]
            
        if is_cancelled:
            order_sale_price = 0.0
            order_cost = 0.0
            order_fees = 0.0
            ship_cost = 0.0
            profit = 0.0
        else:
            profit = order_sale_price - order_cost - order_fees - ship_cost
        
        # Accumulate WooCommerce breakdown
        platform_breakdown["LIRIYA"]["revenue"] += order_sale_price
        platform_breakdown["LIRIYA"]["fees"] += order_fees
        platform_breakdown["LIRIYA"]["shipping_cost"] += ship_cost
        platform_breakdown["LIRIYA"]["profit"] += profit
        
        # Monthly aggregates (combined)
        if month_key not in monthly_data:
            monthly_data[month_key] = {"month": month_key, "revenue": 0, "profit": 0, "shipped": 0, "cancelled": 0, "shipping_cost": 0}
        monthly_data[month_key]['revenue'] += order_sale_price
        monthly_data[month_key]['profit'] += profit
        monthly_data[month_key]['shipping_cost'] += ship_cost
        if is_shipped: monthly_data[month_key]['shipped'] += 1
        if is_cancelled: monthly_data[month_key]['cancelled'] += 1
        
        # Product aggregates (combined)
        for item in line_items:
            sku = item.get("sku")
            if not sku:
                continue
            qty = int(item.get("quantity", 1))
            price = float(item.get("price", 0.0))
            
            if sku not in products_data:
                products_data[sku] = {"sku": sku, "name": item.get("name"), "units_sold": 0, "revenue": 0, "profit": 0}
            products_data[sku]['units_sold'] += qty
            products_data[sku]['revenue'] += price * qty
            item_cost = product_costs.get(sku, 0.0) * qty
            num_items = len(line_items) if len(line_items) > 0 else 1
            item_profit = (price * qty) - item_cost - (order_fees / num_items) - (ship_cost / num_items) if not is_cancelled else 0
            products_data[sku]['profit'] += item_profit

        orders_data.append({
            "amazon_order_id": woo_id,
            "platform": "LIRIYA",
            "date": created_at,
            "status": std_status,
            "sku": primary_sku,
            "sale_price": order_sale_price,
            "product_cost": order_cost,
            "amazon_fees": order_fees,
            "shipping_cost": ship_cost,
            "refunds": 0.0,
            "profit": profit
        })

    # Combined summary totals
    total_orders = sum(p["orders"] for p in platform_breakdown.values())
    total_shipped = sum(p["shipped"] for p in platform_breakdown.values())
    total_cancelled = sum(p["cancelled"] for p in platform_breakdown.values())
    total_revenue = sum(p["revenue"] for p in platform_breakdown.values())
    total_profit = sum(p["profit"] for p in platform_breakdown.values())
    total_shipping_cost = sum(p["shipping_cost"] for p in platform_breakdown.values())
    total_amazon_fees = sum(p["fees"] for p in platform_breakdown.values())

    # Round platform breakdowns
    for p in platform_breakdown:
        for key in ["revenue", "fees", "shipping_cost", "profit"]:
            platform_breakdown[p][key] = round(platform_breakdown[p][key], 2)

    # Prepare output
    output = {
        "last_synced": datetime.now().isoformat(),
        "summary": {
            "total_orders": total_orders,
            "total_shipped": total_shipped,
            "total_cancelled": total_cancelled,
            "total_revenue": round(total_revenue, 2),
            "total_profit": round(total_profit, 2),
            "total_shipping_cost": round(total_shipping_cost, 2),
            "total_amazon_fees": round(total_amazon_fees, 2)
        },
        "platform_breakdown": platform_breakdown,
        "monthly": list(monthly_data.values()),
        "products": list(products_data.values()),
        "orders": orders_data
    }
    
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(output, f, indent=2)
        
    logger.info(f"✅ Sync complete! Data saved to {DATA_FILE}")

if __name__ == "__main__":
    sync_data()
