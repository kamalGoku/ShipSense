import asyncio
import fcntl
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

# Setup paths
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SERVER_DIR)

# Load environment variables BEFORE reading any env-configurable values.
load_dotenv(os.path.join(ROOT_DIR, ".env"))

# Make sure we can import from the parent directory
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import config
from amazon_api import fetch_amazon_new_orders
from shiprocket_api import check_new_orders

STATE_FILE = config.STATE_FILE_PATH
STATIC_DIR = os.path.join(SERVER_DIR, "static")
DASHBOARD_DATA_FILE = os.path.join(SERVER_DIR, "dashboard_data.json")
SYNC_LOCK_FILE = os.path.join(ROOT_DIR, ".sync.lock")

# Timeout (seconds) for the upstream calls behind /api/pending-orders.
PENDING_ORDERS_TIMEOUT = float(os.getenv("PENDING_ORDERS_TIMEOUT", "45"))

app = FastAPI(title="ShipSense UI")

# Ensure static dir exists
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ──────────────────────────────────────────────────
# Auth
#
# Optional shared-secret auth for the API:
#   * If the DASHBOARD_TOKEN env var is set, every /api/* request must supply
#     the same value via the "X-Dashboard-Token" header or a "?token=" query
#     parameter, otherwise it gets a 401.
#   * If DASHBOARD_TOKEN is unset (the default, localhost dev), no auth is
#     required and everything behaves as before.
# The front-end (server/static/sse.js) forwards a token stored in
# localStorage["dashboard_token"] as the X-Dashboard-Token header.
# ──────────────────────────────────────────────────
def require_dashboard_token(request: Request):
    expected = os.getenv("DASHBOARD_TOKEN")
    if not expected:
        return  # Auth disabled (local dev)
    supplied = request.headers.get("X-Dashboard-Token") or request.query_params.get("token") or ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing dashboard token")


api = APIRouter(dependencies=[Depends(require_dashboard_token)])


# ──────────────────────────────────────────────────
# Cross-process sync lock
#
# Only one sync subprocess may run at a time, across all server processes
# (e.g. multiple uvicorn workers or a manually launched sync). We use
# fcntl.flock on a lock file at the repo root; the lock is released
# automatically by the OS if the holding process dies. macOS/Linux only.
# ──────────────────────────────────────────────────
def acquire_sync_lock():
    """Try to take the exclusive sync lock.

    Returns an open file descriptor holding the lock, or None if another
    process already holds it. Caller must pass the fd to release_sync_lock().
    """
    fd = os.open(SYNC_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
    except OSError:
        pass  # PID note is informational only
    return fd


def release_sync_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ──────────────────────────────────────────────────
# SSE protocol
#
# Every SSE frame is "data: <JSON>\n\n" where <JSON> is one of:
#   {"event": "log",      "text": "<output line>"}
#   {"event": "complete", "code": <int exit code>}
# A stream always ends with exactly one "complete" event. Errors (invalid
# command, sync already running, ...) are sent as a "log" event followed by
# a "complete" event with a non-zero code. Consumers: server/static/sse.js.
# ──────────────────────────────────────────────────
def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def sse_error_response(message: str, code: int = 1) -> StreamingResponse:
    async def gen():
        yield sse_event({"event": "log", "text": message})
        yield sse_event({"event": "complete", "code": code})

    return StreamingResponse(gen(), media_type="text/event-stream")


# Allowlist of runnable commands. Anything not here is rejected.
CMD_MAP = {
    "amazon": [sys.executable, "sync_awb.py", "--amazon"],
    "liriya": [sys.executable, "sync_awb.py", "--liriya"],
    "check-new": [sys.executable, "sync_awb.py", "--check-new"],
    "dry-run": [sys.executable, "sync_awb.py", "--dry-run"],
    "sync-dashboard": [sys.executable, "sync_dashboard.py"],
}
# Order / AWB identifiers: alphanumeric plus dashes and underscores.
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_order_ids(raw: str):
    """Validate a comma-separated list of order IDs; return the cleaned string or None."""
    ids = [i.strip() for i in raw.split(",") if i.strip()]
    if not ids or any(not ORDER_ID_RE.match(i) for i in ids):
        return None
    return ",".join(ids)


@app.get("/")
def redirect_index():
    return RedirectResponse(url="/shipments")


@app.get("/shipments")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/revenue")
def dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))


@api.get("/api/state")
def get_state():
    if not os.path.exists(STATE_FILE):
        return {"last_sync": None, "orders": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e), "last_sync": None, "orders": []}


@api.get("/api/dashboard")
def get_dashboard_data():
    if not os.path.exists(DASHBOARD_DATA_FILE):
        return JSONResponse(status_code=404, content={"error": "Dashboard data not found. Please sync first."})
    try:
        with open(DASHBOARD_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}


@api.get("/api/run")
def run_command_get():
    # /api/run has side effects; it is POST-only.
    return JSONResponse(
        status_code=405,
        content={"error": 'Use POST /api/run with a JSON body {"cmd": ..., "orders": ...}'},
    )


@api.post("/api/run")
async def run_command_stream(request: Request):
    """
    Executes an allowlisted command and streams its output back to the client
    using SSE (see "SSE protocol" above). Body: {"cmd": str, "orders": str?}.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    cmd = str(payload.get("cmd") or "")
    orders = payload.get("orders")

    if cmd.startswith("print-order "):
        target_id = cmd.split(" ", 1)[1].strip()
        if not ORDER_ID_RE.match(target_id):
            return sse_error_response(f"Invalid print-order ID '{target_id}'")
        process_cmd = [sys.executable, "sync_awb.py", "--print-order", target_id]
    elif cmd in ("sync-selected-amazon", "sync-selected-liriya"):
        if not orders:
            return sse_error_response("Error: Missing orders parameter")
        cleaned = validate_order_ids(str(orders))
        if cleaned is None:
            return sse_error_response("Error: Invalid orders parameter")
        flag = "--amazon" if cmd == "sync-selected-amazon" else "--liriya"
        process_cmd = [sys.executable, "sync_awb.py", flag, "--orders", cleaned]
    elif cmd in CMD_MAP:
        process_cmd = CMD_MAP[cmd]
    else:
        return sse_error_response(f"Invalid command '{cmd}'")

    async def event_generator():
        lock_fd = acquire_sync_lock()
        if lock_fd is None:
            yield sse_event({"event": "log", "text": "Another sync is already running"})
            yield sse_event({"event": "complete", "code": 1})
            return

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *process_cmd,
                cwd=ROOT_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                decoded_line = line.decode("utf-8", errors="replace").rstrip("\r\n")
                yield sse_event({"event": "log", "text": decoded_line})

            code = await process.wait()
            yield sse_event({"event": "complete", "code": code})
        finally:
            # Runs on normal completion AND on generator close / task
            # cancellation (client disconnect): never leave a zombie
            # subprocess or a held lock behind.
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await process.wait()
                except BaseException:
                    pass  # SIGKILL already delivered; the loop reaps the child
            release_sync_lock(lock_fd)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _parse_order_date(value):
    """Tolerant date parser used only for sorting mixed-format dates.

    Tries ISO 8601 first, then a few common formats; anything unparseable
    sorts last (datetime.min). Returned values are naive UTC so aware and
    naive inputs remain comparable.
    """
    if not value:
        return datetime.min
    s = str(value).strip()
    dt = None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%d %b %Y, %I:%M %p",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@api.get("/api/pending-orders")
async def get_pending_orders_api():
    errors = []

    async def guarded(name, func, *args):
        """Run a blocking upstream call off the event loop with a timeout guard."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func, *args), timeout=PENDING_ORDERS_TIMEOUT
            )
        except asyncio.TimeoutError:
            errors.append(f"{name}: timed out after {PENDING_ORDERS_TIMEOUT:.0f}s")
            return []
        except Exception as e:
            errors.append(f"{name}: {e}")
            return []

    amz_orders, sr_woo_orders = await asyncio.gather(
        guarded("amazon", fetch_amazon_new_orders),
        guarded(
            "woocommerce",
            check_new_orders,
            os.getenv("SHIPROCKET_EMAIL"),
            os.getenv("SHIPROCKET_PASSWORD"),
            config.WOO_CHANNEL_NAME,
        ),
    )

    pending = []

    for o in amz_orders or []:
        pending.append({
            "source": "Amazon",
            "order_id": o.get("amazon_order_id"),
            "status": o.get("status"),
            "date": o.get("created_at"),
            "items": o.get("items_unshipped"),
        })

    for o in sr_woo_orders or []:
        pending.append({
            "source": "WooCommerce",
            "order_id": o.get("channel_order_id") or o.get("id"),
            "status": o.get("status"),
            "date": o.get("created_at"),
            "items": "N/A",  # Shiprocket check_new_orders doesn't easily expose item count at top level
        })

    # Sort by date descending using a tolerant parser (dates arrive in mixed formats)
    pending.sort(key=lambda x: _parse_order_date(x.get("date")), reverse=True)

    result = {"pending_orders": pending}
    if errors:
        result["errors"] = errors
    return result


app.include_router(api)

if __name__ == "__main__":
    import uvicorn

    # Auto-reload is dev-only; enable with UVICORN_RELOAD=1 (default off).
    reload_enabled = os.getenv("UVICORN_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=reload_enabled)
