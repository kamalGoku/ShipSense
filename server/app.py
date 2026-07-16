import asyncio
import json
import os
import sys
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Setup paths
SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SERVER_DIR)
STATE_FILE = os.getenv("STATE_FILE_PATH", os.path.join(ROOT_DIR, "sync_state.json"))
STATIC_DIR = os.path.join(SERVER_DIR, "static")
DASHBOARD_DATA_FILE = os.path.join(SERVER_DIR, "dashboard_data.json")

# Load environment variables
load_dotenv(os.path.join(ROOT_DIR, ".env"))

# Make sure we can import from the parent directory
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from amazon_api import fetch_amazon_new_orders
from shiprocket_api import check_new_orders

app = FastAPI(title="ShipSense UI")


# Ensure static dir exists
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

from fastapi.responses import StreamingResponse, FileResponse, RedirectResponse, JSONResponse

@app.get("/")
def redirect_index():
    return RedirectResponse(url="/shipments")

@app.get("/shipments")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.get("/revenue")
def dashboard():
    return FileResponse(os.path.join(STATIC_DIR, "dashboard.html"))

@app.get("/api/state")
def get_state():
    if not os.path.exists(STATE_FILE):
        return {"last_sync": None, "orders": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e), "last_sync": None, "orders": []}

@app.get("/api/dashboard")
def get_dashboard_data():
    if not os.path.exists(DASHBOARD_DATA_FILE):
        return JSONResponse(status_code=404, content={"error": "Dashboard data not found. Please sync first."})
    try:
        with open(DASHBOARD_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/run")
async def run_command_stream(cmd: str, orders: str = None):
    """
    Executes a command and streams its output back to the client using SSE.
    """
    cmd_map = {
        "amazon": ["python", "sync_awb.py", "--amazon"],
        "liriya": ["python", "sync_awb.py", "--liriya"],
        "check-new": ["python", "sync_awb.py", "--check-new"],
        "dry-run": ["python", "sync_awb.py", "--dry-run"],
        "sync-dashboard": ["python", "sync_dashboard.py"],
    }
    
    if cmd.startswith("print-order "):
        target_id = cmd.split(" ")[1]
        process_cmd = ["python", "sync_awb.py", "--print-order", target_id]
    elif cmd == "sync-selected-amazon":
        if not orders:
            async def error_gen():
                yield f"data: {json.dumps({'text': 'Error: Missing orders parameter'})}\n\n"
                yield f"data: {json.dumps({'text': '[PROCESS_COMPLETE]', 'code': 1})}\n\n"
            return StreamingResponse(error_gen(), media_type="text/event-stream")
        process_cmd = ["python", "sync_awb.py", "--amazon", "--orders", orders]
    elif cmd == "sync-selected-liriya":
        if not orders:
            async def error_gen():
                yield f"data: {json.dumps({'text': 'Error: Missing orders parameter'})}\n\n"
                yield f"data: {json.dumps({'text': '[PROCESS_COMPLETE]', 'code': 1})}\n\n"
            return StreamingResponse(error_gen(), media_type="text/event-stream")
        process_cmd = ["python", "sync_awb.py", "--liriya", "--orders", orders]
    elif cmd in cmd_map:
        process_cmd = cmd_map[cmd]
    else:
        async def error_gen():
            yield f"data: Invalid command '{cmd}'\n\n"
            yield f"data: [PROCESS_COMPLETE]\n\n"
        return StreamingResponse(error_gen(), media_type="text/event-stream")
        
    async def event_generator():
        # Start the subprocess
        process = await asyncio.create_subprocess_exec(
            *process_cmd,
            cwd=ROOT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            # Decode and yield the line (SSE format)
            # Remove the trailing newline for the SSE data payload
            decoded_line = line.decode('utf-8', errors='replace').rstrip('\r\n')
            yield f"data: {json.dumps({'text': decoded_line})}\n\n"
        
        await process.wait()
        yield f"data: {json.dumps({'text': '[PROCESS_COMPLETE]', 'code': process.returncode})}\n\n"
        
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/pending-orders")
def get_pending_orders_api():
    amz_orders = fetch_amazon_new_orders()
    sr_woo_orders = check_new_orders(os.getenv("SHIPROCKET_EMAIL"), os.getenv("SHIPROCKET_PASSWORD"), "LIRIYA (WOOCOMMERCE)")
    
    pending = []
    
    for o in amz_orders:
        pending.append({
            "source": "Amazon",
            "order_id": o.get("amazon_order_id"),
            "status": o.get("status"),
            "date": o.get("created_at"),
            "items": o.get("items_unshipped")
        })
        
    for o in sr_woo_orders:
        pending.append({
            "source": "WooCommerce",
            "order_id": o.get("channel_order_id") or o.get("id"),
            "status": o.get("status"),
            "date": o.get("created_at"),
            "items": "N/A" # Shiprocket check_new_orders doesn't easily expose item count at top level
        })
        
    # Sort by date descending
    pending.sort(key=lambda x: x["date"] if x["date"] else "", reverse=True)
        
    return {"pending_orders": pending}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
