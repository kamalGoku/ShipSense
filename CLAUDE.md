# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

This is a Python automation agent (ShipSense) for e-commerce sellers that bridges **Shiprocket** (courier aggregator), **Amazon SP-API** (Seller Central), and Custom Channels (like WooCommerce). It automates the full post-order workflow: courier assignment → AWB fetching → Amazon shipment confirmation → label download → label printing. Runs headlessly on macOS.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# PyMuPDF (fitz) is already in requirements.txt but can be installed manually if needed
# fping must be installed via Homebrew: brew install fping
cp .env.example .env  # then fill in credentials
```

## Running the Agent

### Using the Web UI (Recommended)
```bash
cd server
uvicorn app:app --port 8000
# Visit http://127.0.0.1:8000 in your browser
```

### Using the CLI
```bash
# Full run: assign couriers + fetch AWBs + confirm on Amazon + print labels
python sync_awb.py --amazon

# Custom Channel run: assign couriers + download labels + print labels for LIRIYA (WOOCOMMERCE)
python sync_awb.py --liriya

# Fetch AWBs, confirm on Amazon, print labels (skip courier assignment)
python sync_awb.py

# Selective sync: process specific orders (comma-separated IDs)
python sync_awb.py --amazon --orders 407-1234567-1234567,402-9876543-2109876
python sync_awb.py --liriya --orders 9274,9275

# Read-only: check unshipped Amazon orders and unassigned Shiprocket orders
python sync_awb.py --check-new

# Print local state summary (no API calls)
python sync_awb.py --status

# Preview all actions without writing anything
python sync_awb.py --dry-run

# Manually batch-convert PDFs to PNG for a date folder
python pdf_tool.py batch labels/DD-MM-YYYY

# List configured printers
python print_tool.py list
```

## Architecture

### Module Responsibilities

| File | Role |
|---|---|
| `sync_awb.py` | Orchestrator — runs phases 0–3 in sequence |
| `sync_dashboard.py` | Aggregates revenue, exact fees, cancellation fees, and fetches data relying on local SQLite caching (`orders_data.db`) |
| `freight_db.py` | Local SQLite DB (`freight/freight_data.db`) tracking Shiprocket freight costs to avoid redundant API hits |
| `order_db.py` | Local SQLite DB (`freight/orders_data.db`) caching Amazon & WooCommerce orders using a delta-sync strategy (default 5 years fallback) |
| `logger.py` | Centralized logging factory with stdout and 10MB rotating file handlers |
| `shiprocket_api.py` | Shiprocket REST client (JWT auth, order fetch, courier assignment) |
| `amazon_api.py` | Amazon SP-API client (LWA OAuth2, order confirmation, finances fees) |
| `state_manager.py` | JSON persistence layer (`sync_state.json`) |
| `pdf_tool.py` | PDF-to-PNG conversion at 300 DPI using PyMuPDF |
| `print_tool.py` | macOS CUPS printer driver with auto-IP discovery via ARP |

### Key Design Patterns

- **Idempotent state machine**: `sync_state.json` tracks per-order booleans (`synced_to_amazon`, `label_printed`). Re-running safely skips completed work. State writes are atomic (`tempfile.mkstemp()` + `os.replace()`).
- **Dry-run flag**: Every mutating operation checks `dry_run: bool` and logs what it *would* do. Use `--dry-run` or set `DRY_RUN=true` in `.env` to preview.
- **Preference-ordered courier fallback**: `auto_assign_amazon_couriers()` tries Delhivery Surface → Delhivery Air → Blue Dart Surface, stopping at first success.
- **Multi-location AWB extraction**: Shiprocket responses are inconsistent across order states; AWB is extracted from three fallback fields (`awb_code`, `shipments[].awb`, `awb_data.awb_code`).
- **Printer auto-discovery**: `print_tool.py` identifies the Epson L3250 by MAC address (`e0:bb:9e:83:1a:02`), pings `192.168.29.0/24` via `fping`, reads the ARP table, and updates CUPS when the DHCP IP changes.
- **Auto-Archiving of Printed Labels**: To prevent duplicate printing when `sync_state.json` is cleared or corrupted, successfully printed labels (PDF and PNG) are immediately moved to a `labels/DD-MM-YYYY/printed/` subfolder.
- **Centralized Logging**: `logger.py` provides operational logs to console and rotating `logs/agent.log` (10MB max, 5 backups). Prevents duplicate handlers during Uvicorn hot-reloads. Standard `print()` is only retained for structured CLI tables.

### API Endpoints

- **Shiprocket**: `https://apiv2.shiprocket.in/v1/external` — JWT bearer token, refreshed each run
- **Amazon SP-API**: `https://sellingpartnerapi-eu.amazon.com` — India uses the EU regional endpoint; OAuth2 LWA refresh token flow with 5-minute buffer in-memory caching

### Amazon Order Filtering

Shiprocket orders are matched to Amazon using a regex for Amazon order IDs (`\d{3}-\d{7}-\d{7}`) and filtered by channel name `"LIRIYA (AMAZON)"`. Only the last 5 pages (500 orders) are fetched from Shiprocket.

## Environment Variables

| Variable | Purpose |
|---|---|
| `SHIPROCKET_EMAIL` / `SHIPROCKET_PASSWORD` | Shiprocket login |
| `AMAZON_CLIENT_ID` / `AMAZON_CLIENT_SECRET` / `AMAZON_REFRESH_TOKEN` | Amazon LWA OAuth2 |
| `AMAZON_MARKETPLACE_ID` | Defaults to `A21TJRUUN4KGV` (India) |
| `WOOCOMMERCE_STORE_URL` | WooCommerce base URL (e.g. `https://liriya.com`) |
| `WOOCOMMERCE_CONSUMER_KEY` / `WOOCOMMERCE_CONSUMER_SECRET` | WooCommerce REST API keys |
| `STATE_FILE_PATH` | Path to JSON state file (default: `./sync_state.json`) |
| `DRY_RUN` | Set to `"true"` to preview without making changes |

## Important Notes

- `sync_state.json` is the live production database for the sync_awb pipeline — treat it carefully. It contains real order records.
- `freight/orders_data.db` and `freight/freight_data.db` are SQLite databases caching history for the revenue dashboard. They drastically reduce API costs.
- `labels/` contains production shipping label PDFs/PNGs organized by date (`DD-MM-YYYY/`).
- A comprehensive headless `pytest` suite is located in the `tests/` directory. Run tests using `python -m pytest tests/ -v`.
- PyMuPDF (`fitz`), `pytest`, and `pytest-mock` are included in `requirements.txt` and are required for full functionality.
