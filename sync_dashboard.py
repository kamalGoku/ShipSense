import json
import csv
import os
import time
from datetime import datetime, timedelta, timezone
import requests
from requests.auth import HTTPBasicAuth

import config
from amazon_api import fetch_amazon_all_orders, get_order_items_details, fetch_order_finances
from shiprocket_api import _get_api_token
from logger import get_logger
from freight_db import FreightDB
from order_db import OrderDB

logger = get_logger(__name__)

DATA_FILE = "server/dashboard_data.json"
COSTS_FILE = "product_costs.csv"

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
    if os.path.exists(config.STATE_FILE_PATH):
        with open(config.STATE_FILE_PATH, "r") as f:
            try:
                return json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ Could not parse sync state file {config.STATE_FILE_PATH}: {e}")
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

    try:
        token = _get_api_token(email, password)
    except Exception as e:
        logger.error(f"❌ Shiprocket auth error: {e}")
        return {}
    if not token:
        logger.error("❌ Shiprocket auth failed for freight fetch")
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
            if sr_id.startswith(config.WOO_STATE_PREFIX) and amz_id:
                # Search by channel order id
                resp = requests.get(
                    f"{config.SHIPROCKET_API_BASE}/orders",
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
                    f"{config.SHIPROCKET_API_BASE}/orders/show/{sr_id}",
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

def fetch_woocommerce_orders(days_back: int = config.INITIAL_SYNC_DAYS, modified_after: str = None) -> list:
    """Fetch orders from WooCommerce REST API.

    Raises on any failure (missing credentials or HTTP/network error) so
    callers can distinguish "fetch failed" from "no new orders".
    """
    url = f"{os.getenv('WOOCOMMERCE_STORE_URL')}/wp-json/wc/v3/orders"
    ck = os.getenv('WOOCOMMERCE_CONSUMER_KEY')
    cs = os.getenv('WOOCOMMERCE_CONSUMER_SECRET')
    if not os.getenv('WOOCOMMERCE_STORE_URL') or not ck or not cs:
        raise ValueError("WooCommerce credentials missing in .env")

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

def _finances_fetched(finances) -> bool:
    """True if real financial data was already fetched for this order.

    New records carry an explicit 'fetched' flag (True even for genuinely
    zero-fee orders, so they are not re-fetched every run). Legacy records
    lack the flag; for those fall back to the old heuristic that nonzero
    fees imply a completed fetch.
    """
    if not finances:
        return False
    fees = finances.get('fees', finances.get('total_amazon_fees', 0.0))
    return bool(finances.get('fetched', fees != 0.0))

def _build_awb_index(sync_state) -> dict:
    """Index sync_state entries by channel order id for O(1) AWB lookup.

    Keeps the first entry per id, matching the previous first-match scan.
    """
    index = {}
    for entry in sync_state.get("orders", []):
        key = entry.get("amazon_order_id")
        if key is not None and key not in index:
            index[key] = entry.get("awb_number")
    return index

def _lookup_ship_cost(freight_costs, awb_index, keys) -> float:
    """Resolve shipping cost: direct order-id keys first, then via the AWB
    recorded in sync_state for any of those keys."""
    for key in keys:
        if key in freight_costs:
            return freight_costs[key]
    for key in keys:
        awb = awb_index.get(key)
        if awb and awb in freight_costs:
            return freight_costs[awb]
    return 0.0

def _sync_amazon_to_db(order_db) -> None:
    """Phase 1a: fetch the Amazon order delta and upsert it into the cache DB.

    Advances the Amazon watermark only if the fetch succeeded AND every order
    in the delta was processed. If any order fails, the watermark stays put and
    the whole delta is refetched next run (upserts are idempotent).
    Raises if the fetch itself fails.
    """
    last_synced = order_db.get_last_synced_timestamp("amazon")
    # Capture the new watermark BEFORE fetching so orders updated while this
    # run is in flight are still picked up by the next sync.
    new_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if last_synced:
        amazon_orders_delta = fetch_amazon_all_orders(last_updated_after=last_synced)
    else:
        amazon_orders_delta = fetch_amazon_all_orders(days_back=config.INITIAL_SYNC_DAYS)

    logger.info(f"📦 Found {len(amazon_orders_delta)} Amazon orders to update.")

    failed_order_ids = []
    for order in amazon_orders_delta:
        amz_id = order['amazon_order_id']
        try:
            status = order['status']
            created_at = order['created_at']
            updated_at = order.get('updated_at', created_at)

            existing_order = order_db.get_order(amz_id)

            items = existing_order['items'] if existing_order else None
            if not items:
                items = get_order_items_details(amz_id)
            if not items:
                raise ValueError("no order items returned by SP-API")

            is_cancelled = status == 'Canceled'
            finances = existing_order['finances'] if existing_order else None

            # Fetch finances if shipped and not previously fetched. The stored
            # 'fetched' flag distinguishes genuinely-zero-fee orders from
            # never-fetched ones, so zeros are not re-fetched every run.
            if not is_cancelled and not _finances_fetched(finances):
                time.sleep(2)  # 0.5 req/s limit
                finances = fetch_order_finances(amz_id)

            if finances is None:
                finances = {}
            if 'total' in order:
                finances['total'] = order['total']

            order_db.upsert_order(
                order_id=amz_id,
                platform=config.AMAZON_PLATFORM_KEY,
                status=status,
                created_at=created_at,
                updated_at=updated_at,
                items=items,
                finances=finances
            )
        except Exception as e:
            logger.error(f"❌ Failed to process Amazon order {amz_id}: {e}")
            failed_order_ids.append(amz_id)

    if failed_order_ids:
        logger.error(
            f"⚠️ {len(failed_order_ids)} Amazon order(s) failed to process: {failed_order_ids}. "
            "Amazon watermark NOT advanced; the delta will be refetched next run."
        )
    else:
        order_db.set_last_synced_timestamp(new_timestamp, "amazon")

def _sync_woocommerce_to_db(order_db) -> None:
    """Phase 1b: fetch the WooCommerce order delta and upsert it into the cache DB.

    Same watermark rules as _sync_amazon_to_db. Raises if the fetch fails.
    """
    last_synced = order_db.get_last_synced_timestamp("woocommerce")
    new_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if last_synced:
        woo_orders_delta = fetch_woocommerce_orders(modified_after=last_synced)
    else:
        woo_orders_delta = fetch_woocommerce_orders(days_back=config.INITIAL_SYNC_DAYS)

    logger.info(f"📦 Found {len(woo_orders_delta)} WooCommerce orders to update.")

    failed_order_ids = []
    for woo_order in woo_orders_delta:
        woo_id = str(woo_order.get("id"))
        try:
            status = woo_order.get("status")
            created_at = woo_order.get("date_created")  # ISO string
            updated_at = woo_order.get("date_modified") or created_at

            # Standardize WooCommerce statuses
            if status in ['completed', 'processing']:
                std_status = 'Shipped'
            elif status in ['cancelled', 'refunded', 'failed']:
                std_status = 'Canceled'
            else:
                std_status = 'Pending'

            line_items = woo_order.get("line_items", [])

            order_db.upsert_order(
                order_id=woo_id,
                platform=config.WOO_PLATFORM_KEY,
                status=std_status,
                created_at=created_at,
                updated_at=updated_at,
                items=line_items,
                finances={
                    "total": woo_order.get("total", "0.0"),
                    "gateway": woo_order.get("payment_method_title", woo_order.get("payment_method", "Unknown"))
                }
            )
        except Exception as e:
            logger.error(f"❌ Failed to process WooCommerce order {woo_id}: {e}")
            failed_order_ids.append(woo_id)

    if failed_order_ids:
        logger.error(
            f"⚠️ {len(failed_order_ids)} WooCommerce order(s) failed to process: {failed_order_ids}. "
            "WooCommerce watermark NOT advanced; the delta will be refetched next run."
        )
    else:
        order_db.set_last_synced_timestamp(new_timestamp, "woocommerce")

def _normalize_amazon_order(order, product_costs, freight_costs, awb_index) -> dict:
    """Convert a cached Amazon order row into the canonical order dict."""
    amz_id = order['order_id']
    status = order['status']
    created_at = order['created_at']
    is_shipped = status == 'Shipped'
    is_cancelled = status == 'Canceled'

    canonical = {
        "id": amz_id,
        "platform_key": config.AMAZON_PLATFORM_KEY,
        "status": status,
        "date": created_at,
        # Buckets by the stored timestamp's date (UTC for Amazon purchase dates).
        "month_key": created_at[:7],
        "is_shipped": is_shipped,
        "is_cancelled": is_cancelled,
        "skip_details": False,
    }

    items = order['items']
    if not items:
        # No item details cached (order failed to fully sync). Count it in
        # order totals but skip all financial detail, as before.
        canonical["skip_details"] = True
        return canonical

    finances_safe = order['finances'] or {}
    if 'total' in finances_safe:
        sale_price = float(finances_safe['total'])
    else:
        sale_price = sum(i['price'] for i in items)
    product_cost = sum(product_costs.get(i['sku'], 0.0) * i['quantity'] for i in items)

    fees = 0.0
    refunds = 0.0
    fees_estimated = False
    if not is_cancelled:
        finances = order['finances'] or {}
        fees = abs(finances.get('fees', finances.get('total_amazon_fees', 0.0)))
        refunds = abs(finances.get('refunds', finances.get('total_refunds', 0.0)))
        if not _finances_fetched(finances) and sale_price > 0.0:
            # Real fees unavailable: estimate and flag it in the output.
            fees = round(sale_price * config.ESTIMATED_AMAZON_FEE_RATE, 2)
            fees_estimated = True

    ship_cost = _lookup_ship_cost(freight_costs, awb_index, [amz_id])

    is_full_refund = False
    if is_cancelled:
        sale_price = 0.0
        product_cost = 0.0
        fees = 0.0
        ship_cost = 0.0
        profit = 0.0
    else:
        # A full refund means the inventory came back to the shelf (or never
        # left), so COGS is recovered (set to 0). A partial refund implies a
        # concession where the customer kept the item, so COGS remains.
        is_full_refund = refunds > 0 and refunds >= (sale_price - config.FULL_REFUND_TOLERANCE)
        if is_full_refund:
            product_cost = 0.0
        profit = sale_price - product_cost - fees - ship_cost - refunds

    norm_items = [
        {
            "sku": item.get('sku') or "UNKNOWN",
            "name": item['title'],
            "line_revenue": item['price'],
            "quantity": item['quantity'],
        }
        for item in items
    ]

    canonical.update({
        "sale_price": sale_price,
        "fees": fees,
        "refunds": refunds,
        "shipping_cost": ship_cost,
        "product_cost": product_cost,
        "profit": profit,
        "sku": items[0]['sku'] if items else "UNKNOWN",
        "product_name": items[0].get('title') if items else None,
        "fees_estimated": fees_estimated,
        "is_full_refund": is_full_refund,
        "items": norm_items,
        "num_items": max(len(items), 1),
    })
    return canonical

def _normalize_woo_order(order, product_costs, freight_costs, awb_index) -> dict:
    """Convert a cached WooCommerce order row into the canonical order dict."""
    woo_id = order['order_id']
    status = order['status']
    created_at = order['created_at']
    is_shipped = status == 'Shipped'
    is_cancelled = status == 'Canceled'

    line_items = order['items'] or []
    finances = order.get('finances') or {}

    # Use the full gross order total if we captured it, otherwise fallback to item sum.
    if 'total' in finances:
        sale_price = float(finances['total'])
    else:
        sale_price = sum(float(i.get('total', 0.0)) for i in line_items)
    product_cost = sum(product_costs.get(i.get("sku"), 0.0) * int(i.get("quantity", 1)) for i in line_items)

    # Payment gateway fee on WooCommerce payments
    fees = round(sale_price * config.PAYMENT_GATEWAY_FEE_RATE, 2) if not is_cancelled else 0.0

    ship_cost = _lookup_ship_cost(
        freight_costs, awb_index,
        [woo_id, f"{config.WOO_STATE_PREFIX}{woo_id}"]
    )

    if is_cancelled:
        sale_price = 0.0
        product_cost = 0.0
        fees = 0.0
        ship_cost = 0.0
        profit = 0.0
    else:
        profit = sale_price - product_cost - fees - ship_cost

    norm_items = []
    for item in line_items:
        sku = item.get("sku")
        if not sku:
            continue
        qty = int(item.get("quantity", 1))
        price = float(item.get("price", 0.0))
        norm_items.append({
            "sku": sku,
            "name": item.get("name"),
            "line_revenue": price * qty,
            "quantity": qty,
        })

    return {
        "id": woo_id,
        "platform_key": config.WOO_PLATFORM_KEY,
        "status": status,
        "date": created_at,
        # Buckets by the stored timestamp's date.
        "month_key": created_at[:7],
        "is_shipped": is_shipped,
        "is_cancelled": is_cancelled,
        "skip_details": False,
        "sale_price": sale_price,
        "fees": fees,
        "refunds": 0.0,
        "shipping_cost": ship_cost,
        "product_cost": product_cost,
        "profit": profit,
        "sku": line_items[0].get("sku") if line_items else "UNKNOWN",
        "product_name": line_items[0].get("name") if line_items else None,
        "fees_estimated": False,
        "is_full_refund": False,
        "items": norm_items,
        "num_items": max(len(line_items), 1),
    }

def sync_data():
    logger.info("🚀 Starting Combined Dashboard Sync...")

    product_costs = load_product_costs()
    freight_costs = load_freight_costs()
    sync_state = load_sync_state()

    # Augment freight data with Shiprocket API for orders not in CSV
    api_freight = fetch_api_freight_costs(sync_state, freight_costs)
    freight_costs.update(api_freight)
    logger.info(f"📊 Total freight entries: {len(freight_costs)} (CSV + API)")

    order_db = OrderDB()

    # ── Phase 1: refresh the local order cache from both platforms ──
    # A fetch failure raises; we log it, leave that platform's watermark
    # untouched, and still render the dashboard from cached data.
    try:
        _sync_amazon_to_db(order_db)
    except Exception as e:
        logger.error(f"❌ Amazon fetch failed ({e}). Watermark NOT advanced; rendering from cached data.")

    try:
        _sync_woocommerce_to_db(order_db)
    except Exception as e:
        logger.error(f"❌ WooCommerce fetch failed ({e}). Watermark NOT advanced; rendering from cached data.")

    # ── Phase 2: aggregate. Read the cache once, AFTER both platforms have
    # been upserted, so first-run orders from either platform appear in this
    # run's output. ──
    all_cached_orders = order_db.get_all_orders()
    awb_index = _build_awb_index(sync_state)

    normalized_orders = [
        _normalize_amazon_order(o, product_costs, freight_costs, awb_index)
        for o in all_cached_orders if o['platform'] == config.AMAZON_PLATFORM_KEY
    ] + [
        _normalize_woo_order(o, product_costs, freight_costs, awb_index)
        for o in all_cached_orders if o['platform'] == config.WOO_PLATFORM_KEY
    ]

    platform_breakdown = {
        key: {"orders": 0, "shipped": 0, "cancelled": 0, "revenue": 0.0, "fees": 0.0,
              "shipping_cost": 0.0, "profit": 0.0, "cancellation_fees": 0.0}
        for key in (config.AMAZON_PLATFORM_KEY, config.WOO_PLATFORM_KEY)
    }
    monthly_data = {}
    products_data = {}
    orders_data = []

    for o in normalized_orders:
        pb = platform_breakdown[o["platform_key"]]
        pb["orders"] += 1
        if o["is_shipped"]:
            pb["shipped"] += 1
        elif o["is_cancelled"]:
            pb["cancelled"] += 1

        if o["skip_details"]:
            continue

        pb["revenue"] += o["sale_price"]
        pb["fees"] += o["fees"]
        pb["shipping_cost"] += o["shipping_cost"]
        pb["profit"] += o["profit"]
        if o["is_full_refund"]:
            pb["cancellation_fees"] += o["fees"]

        # Monthly aggregates (combined across platforms)
        month = monthly_data.setdefault(o["month_key"], {
            "month": o["month_key"], "revenue": 0, "profit": 0,
            "shipped": 0, "cancelled": 0, "shipping_cost": 0
        })
        month['revenue'] += o["sale_price"]
        month['profit'] += o["profit"]
        month['shipping_cost'] += o["shipping_cost"]
        if o["is_shipped"]:
            month['shipped'] += 1
        if o["is_cancelled"]:
            month['cancelled'] += 1

        # Product aggregates (combined across platforms)
        if not o["is_cancelled"]:
            for item in o["items"]:
                sku = item["sku"]
                product = products_data.setdefault(sku, {
                    "sku": sku, "name": item["name"], "units_sold": 0, "revenue": 0, "profit": 0
                })
                product['units_sold'] += item["quantity"]
                product['revenue'] += item["line_revenue"]
                item_cost = product_costs.get(sku, 0.0) * item["quantity"]
                item_profit = (item["line_revenue"] - item_cost
                               - (o["fees"] / o["num_items"])
                               - (o["shipping_cost"] / o["num_items"]))
                product['profit'] += item_profit

        orders_data.append({
            "amazon_order_id": o["id"],
            "platform": o["platform_key"],
            "date": o["date"],
            "status": o["status"],
            "sku": o["sku"],
            "sale_price": o["sale_price"],
            "product_cost": o["product_cost"],
            "amazon_fees": o["fees"],
            "shipping_cost": o["shipping_cost"],
            "refunds": o["refunds"],
            "profit": o["profit"],
            "fees_estimated": o["fees_estimated"]
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
