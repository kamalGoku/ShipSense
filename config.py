"""
Central configuration for ShipSense.

All environment-specific and business-specific values live here so that
adding a new sales channel, courier, or deployment environment does not
require editing business logic. Values can be overridden via environment
variables (loaded from .env); defaults preserve the existing production
behavior and on-disk data formats.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────
# Sales channels
# NOTE: These strings are persisted in sync_state.json and orders_data.db.
# Changing them on an existing installation orphans historical records.
# ──────────────────────────────────────────────────
AMAZON_CHANNEL_NAME = os.getenv("AMAZON_CHANNEL_NAME", "LIRIYA (AMAZON)")
WOO_CHANNEL_NAME = os.getenv("WOO_CHANNEL_NAME", "LIRIYA (WOOCOMMERCE)")

AMAZON_PLATFORM_KEY = os.getenv("AMAZON_PLATFORM_KEY", "Amazon (LIRIYA)")
WOO_PLATFORM_KEY = os.getenv("WOO_PLATFORM_KEY", "LIRIYA")

# Prefix used for synthetic WooCommerce entries in sync_state.json
WOO_STATE_PREFIX = "LIRIYA_"

# ──────────────────────────────────────────────────
# Amazon SP-API
# ──────────────────────────────────────────────────
AMAZON_MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "A21TJRUUN4KGV")
SPAPI_BASE_URL = os.getenv("SPAPI_BASE_URL", "https://sellingpartnerapi-eu.amazon.com")

# Shiprocket courier name → (carrierCode, carrierName) for SP-API confirmShipment.
# Matched as case-insensitive substrings, first match wins.
CARRIER_SPAPI_MAP = [
    ("delhivery", ("Delhivery", "Delhivery")),
    ("blue dart", ("BlueDart", "BlueDart")),
    ("bluedart", ("BlueDart", "BlueDart")),
    ("ecom", ("Ecom Express", "Ecom Express")),
    ("xpressbees", ("Xpressbees", "Xpressbees")),
    ("ekart", ("Ekart", "Ekart")),
]

# ──────────────────────────────────────────────────
# Shiprocket
# ──────────────────────────────────────────────────
SHIPROCKET_API_BASE = os.getenv(
    "SHIPROCKET_API_BASE", "https://apiv2.shiprocket.in/v1/external"
)
# Courier names as they appear in Shiprocket serviceability, in preference order
COURIER_PREFERENCE = [
    c.strip()
    for c in os.getenv(
        "COURIER_PREFERENCE",
        "Delhivery Surface,Delhivery Air,Blue Dart Surface",
    ).split(",")
    if c.strip()
]
SHIPROCKET_MAX_PAGES = int(os.getenv("SHIPROCKET_MAX_PAGES", "5"))
SHIPROCKET_PAGE_SIZE = int(os.getenv("SHIPROCKET_PAGE_SIZE", "100"))

# ──────────────────────────────────────────────────
# Financial assumptions (dashboard estimates)
# ──────────────────────────────────────────────────
# Fee rate assumed when real Amazon fees are unavailable (estimate, flagged in output)
ESTIMATED_AMAZON_FEE_RATE = float(os.getenv("ESTIMATED_AMAZON_FEE_RATE", "0.25"))
# WooCommerce payment gateway fee rate
PAYMENT_GATEWAY_FEE_RATE = float(os.getenv("PAYMENT_GATEWAY_FEE_RATE", "0.02"))
# Refund within this amount of sale price is treated as a full refund (INR)
FULL_REFUND_TOLERANCE = float(os.getenv("FULL_REFUND_TOLERANCE", "1.0"))
# How far back the first-ever sync looks (days)
INITIAL_SYNC_DAYS = int(os.getenv("INITIAL_SYNC_DAYS", "1825"))

# ──────────────────────────────────────────────────
# HTTP behavior
# ──────────────────────────────────────────────────
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "20"))
RATE_LIMIT_RETRIES = int(os.getenv("RATE_LIMIT_RETRIES", "3"))
RATE_LIMIT_BACKOFF_SECONDS = float(os.getenv("RATE_LIMIT_BACKOFF_SECONDS", "3"))

# ──────────────────────────────────────────────────
# Printer
# ──────────────────────────────────────────────────
PRINTER_MAC = os.getenv("PRINTER_MAC", "e0:bb:9e:83:1a:02")
PRINTER_SUBNET = os.getenv("PRINTER_SUBNET", "192.168.29.0/24")

# ──────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE_PATH = os.getenv(
    "STATE_FILE_PATH", os.path.join(ROOT_DIR, "sync_state.json")
)
ORDERS_DB_PATH = os.getenv(
    "ORDERS_DB_PATH", os.path.join(ROOT_DIR, "freight", "orders_data.db")
)
FREIGHT_DB_PATH = os.getenv(
    "FREIGHT_DB_PATH", os.path.join(ROOT_DIR, "freight", "freight_data.db")
)
LABELS_DIR = os.getenv("LABELS_DIR", os.path.join(ROOT_DIR, "labels"))
LOG_DIR = os.getenv("LOG_DIR", os.path.join(ROOT_DIR, "logs"))
