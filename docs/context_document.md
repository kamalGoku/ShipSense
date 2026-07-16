# ShipSense (Multi-Channel AWB Sync): Codebase Context

This document provides a comprehensive overview of the `ShipSense` codebase, explaining its architecture, workflows, integrations, and state management logic.

## Overview
The application is a headless Python automation agent for an Amazon seller ("LIRIYA"). It bridges **Shiprocket** (a courier aggregator) and **Amazon SP-API** (Seller Central). The system fully automates the post-order workflow, taking orders from the "New" state, assigning couriers, fetching tracking numbers (AWBs), confirming shipments on Amazon, and physically printing shipping labels.

Additionally, it includes a **Business Revenue and Profit Dashboard** that syncs historical orders from both Amazon SP-API and WooCommerce, matches actual shipping fees from monthly Shiprocket freight invoices, calculates COGS from a local CSV database, and caches data to render a glassmorphic analytics UI.

## Architecture & File Structure

The project is heavily modularized, separating orchestration, state, APIs, and OS-level utilities.

### 1. Orchestration & Aggregation
- **`sync_awb.py`**: The entry point and main orchestrator for the shipping/fulfillment workflow. It executes the synchronization workflow in defined phases:
  - **Phase 0**: Assign couriers to "NEW" Amazon orders and download/generate labels.
  - **Phase 1**: Fetch active orders and AWBs from Shiprocket and update local state.
  - **Phase 2**: Submit AWB tracking details to Amazon SP-API.
  - **Phase 3 (PDF/Print)**: Convert downloaded PDF labels to PNG images and send them to the printer.
- **`sync_dashboard.py`**: The entry point and aggregation script for business intelligence. It:
  - Loads product cost mappings from `product_costs.csv`.
  - Scans `freight/` for Shiprocket invoice CSVs to extract actual shipping costs.
  - Fetches historical orders from Amazon SP-API and WooCommerce.
  - Batch-requests fee profiles from the Amazon Finances API.
  - Calculates net profit per order and writes a unified cache to `server/dashboard_data.json`.

### 2. Integrations
- **`shiprocket_api.py`**: Interacts with the Shiprocket REST API. 
  - Authenticates via JWT.
  - Checks courier serviceability and auto-assigns preferred couriers (Delhivery Surface -> Delhivery Air -> Blue Dart Surface).
  - Downloads labels as PDFs to `labels/DD-MM-YYYY/`.
  - Schedules pickups.
  - Fetches recent orders to scrape AWB numbers for Amazon.
- **`amazon_api.py`**: Interacts with the Amazon Selling Partner API (SP-API).
  - Authenticates via LWA (Login with Amazon) OAuth2 using a refresh token.
  - Fetches unshipped Amazon orders.
  - Fetches shipped and canceled historical orders supporting `NextToken` pagination.
  - Fetches Order Items (a mandatory prerequisite for shipment confirmation) and confirms shipments.
  - Fetches order financial events to extract exact Amazon fees (referral, commission) and adjustment refund events, with built-in retries for 429 rate limit errors.
  - Translates Shiprocket carrier names to Amazon SP-API carrier codes (e.g., `Delhivery`, `BlueDart`).
- **WooCommerce REST Integration (via requests in `sync_dashboard.py`)**:
  - Connects to the WooCommerce store API using Basic Auth (Consumer Key/Secret).
  - Paginate-fetches historical orders to extract item quantities, prices, and statuses.

### 3. State & Data Management
- **`state_manager.py`** & **`sync_state.json`**: Implements an idempotent state machine.
  - `sync_state.json` is the local "database" containing order records, their statuses (`synced_to_amazon`, `label_printed`), and error logs.
  - State files are saved atomically (written to a temporary file, then renamed) to prevent corruption during unexpected crashes.
  - Prevents double-syncing or double-printing by safely skipping completed work.
- **`product_costs.csv`**: A local CSV file that maps Seller SKUs to their cost price (COGS) for accurate margin calculations.
- **`freight/`**: A local directory containing exported Shiprocket freight invoices. The dashboard aggregator automatically scans and parses CSVs in this folder to lookup shipping costs by Order ID or AWB number.

### 4. Media & Printing Tools
- **`pdf_tool.py`**: Uses PyMuPDF (`fitz`) to crop and extract high-quality (300 DPI) PNG images from the downloaded Shiprocket PDF labels.
- **`print_tool.py`**: A macOS-specific hardware utility for an Epson L3250 printer.
  - Uses `fping` and `arp` to proactively discover the printer on the local network (`192.168.29.0/24`) based on its hardcoded MAC address (`e0:bb:9e:83:1a:02`).
  - Auto-updates the macOS CUPS device URI (`ipp://[IP]/ipp/print`) if the DHCP lease changes.
  - Dispatches print jobs using the macOS `lp` CLI command (sized to A6).

### 5. Testing Suite
- **`tests/`**: Contains a robust headless test suite built with `pytest` and `pytest-mock`.
  - Simulates API responses for SP-API, WooCommerce, and Shiprocket.
  - Validates API pagination logic (`NextToken` and WooCommerce page parameters).
  - Mocks rate limit retries (handling HTTP 429 backoff) and HTTP 404 financial errors.
  - Tests sync aggregation scenarios, verifying platform fee allocations, cancelled order logic, and freight lookup fallbacks.
  - Capable of being run fully decoupled from network via `pytest`.

### 6. Web UI Dashboard
- **`server/app.py`**: A FastAPI application that serves as the backend for the GUI. It mounts static files, serves `sync_state.json` via `/api/state`, reads/caches dashboard summary metrics, and runs execution processes asynchronously, streaming terminal logs to the browser via SSE (Server-Sent Events).
- **`server/static/`**: Contains the Vanilla HTML, CSS, and JS for:
  - **Fulfillment Operations (`/shipments`)**: Unified order queues and logs.
  - **Business Revenue Dashboard (`/revenue`)**: A glassmorphism analytics dashboard utilizing Chart.js to report combined sales breakdowns, monthly revenue/profit projections, and top-selling product tables.

### 7. Centralized Logging
- **`logger.py`**: A centralized logging factory that provides operational logs to both the console (stdout) and a rotating file (`logs/agent.log`).
  - Implements 10MB file rotation with up to 5 backups to prevent bounded disk growth.
  - Features duplicate handler guards for Uvicorn hot-reloads.
  - Replaces raw `print()` statements across the codebase, except for CLI structured ASCII tables.

## Key Workflows

### Full Sync Loop (`--amazon`)
1. Fetches "NEW", "AWB ASSIGNED", or "READY TO SHIP" Amazon orders from Shiprocket.
2. Checks courier serviceability, assigns the best courier, and schedules the pickup.
3. Requests and downloads the shipping label to a local directory.
4. Pulls a fresh list of recent Shiprocket orders to capture AWB tracking data.
5. Pushes tracking updates to Amazon SP-API (idempotent, relies on `sync_state.json`).
6. Looks at newly downloaded PDFs, converts them to images, and sends them to the local printer.

### Custom Channel Loop (`--liriya`)
Bypasses the Amazon SP-API entirely to process independent channels (e.g., `"LIRIYA (WOOCOMMERCE)"`).
1. Fetches "NEW" orders specifically for the target channel.
2. Assigns couriers, schedules pickup, and downloads the PDF labels.
3. Skips Shiprocket AWB scraping and Amazon SP-API submission.
4. Converts and prints the labels. Injects a dummy state entry (`LIRIYA_{id}`) into `sync_state.json` to ensure print idempotency (preventing duplicate prints).

### Combined Dashboard Sync (`sync-dashboard`)
1. Reads `product_costs.csv` and parses invoice CSVs in `freight/`.
2. Fetches Amazon shipped and canceled orders, pulling order items and finances (using a 25% fallback if finances are missing).
3. Fetches WooCommerce orders (applying a 2% gateway fee fallback).
4. Matches freight invoices by order number or AWB number from sync state.
5. Calculates margins: `Sale Price - COGS - Fees - Freight`. Zeroes out calculations for cancelled/returned orders.
6. Computes aggregations by product, month, and platform, then dumps to `server/dashboard_data.json`.

### Dry Run Mode (`--dry-run`)
Safety mechanism baked into all modifying functions. When triggered, it executes API reads and local logic but skips all POST/PUT actions (e.g., assigning couriers, submitting tracking to Amazon, saving `sync_state.json`, and issuing print commands), printing what *would* happen instead.

### Specific Order Print (`--print-order <ID>`)
Bypasses the standard sync phases. Looks up a specific Amazon Order ID, Shiprocket ID, or AWB in the local state, forcefully re-downloads the label from Shiprocket via the `/orders/show/` endpoint, and prints it immediately.

## Error Handling & Reliability
- **Timeouts & Retries**: External API calls are guarded with `requests` timeouts to prevent hanging.
- **Rate-Limit Handling**: Uses linear and exponential backoff strategies to automatically retry on SP-API rate limits (429).
- **Network Discovery**: Rather than failing when a printer IP changes, `print_tool.py` dynamically scans the ARP table to remap the IP address via CUPS.
- **State Integrity**: Any failures in confirming shipments to Amazon SP-API are logged in the state file (`error` field) without marking the order as `synced_to_amazon`, ensuring the script will retry on the next run.
- **Operational Auditing**: `logger.py` centrally handles all runtime warnings and errors with timestamps, file line numbers, and log rotation, ensuring background failures are always traceable without bloating disk storage.
