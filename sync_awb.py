import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

# ── Load environment early ───────────────────────────────────────────
load_dotenv()

from state_manager import (
    load_state, save_state, merge_orders,
    get_pending_orders, mark_synced, mark_error,
    mark_printed,
)
from shiprocket_api import (
    scrape_shiprocket_api, auto_assign_couriers,
    check_new_orders,
)
from amazon_api import confirm_shipment_api, fetch_amazon_new_orders
import pdf_tool
from print_tool import print_image
from logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# ── Load environment ─────────────────────────────────────────────────
load_dotenv()

SHIPROCKET_EMAIL    = os.getenv("SHIPROCKET_EMAIL", "")
SHIPROCKET_PASSWORD = os.getenv("SHIPROCKET_PASSWORD", "")
STATE_FILE          = os.getenv("STATE_FILE_PATH", "./sync_state.json")
DRY_RUN_ENV         = os.getenv("DRY_RUN", "false").lower() == "true"


def print_status(state: dict) -> None:
    """Print a summary of the current state."""
    total  = len(state["orders"])
    synced = sum(1 for o in state["orders"] if o.get("synced_to_amazon"))
    pending = sum(1 for o in state["orders"]
                  if not o.get("synced_to_amazon") and o.get("awb_number") and o.get("awb_number") != "No AWB")
    errored = sum(1 for o in state["orders"] if o.get("error"))

    print("=" * 60)
    print("  AWB Sync Status (100% API)")
    print("=" * 60)
    print(f"  Last sync    : {state.get('last_sync', 'Never')}")
    print(f"  Total orders : {total}")
    print(f"  Synced       : {synced}")
    print(f"  Pending      : {pending}")
    print(f"  Errors       : {errored}")
    print("=" * 60)

    if errored:
        print("\n  Orders with errors:")
        for o in state["orders"]:
            if o.get("error"):
                print(f"    - SR#{o['shiprocket_order_id']} "
                      f"(AMZ#{o['amazon_order_id']}): {o['error']}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Sync AWB numbers from Shiprocket API to Amazon SP-API"
    )
    parser.add_argument("--dry-run", action="store_true", default=DRY_RUN_ENV,
                        help="Preview sync but skip Amazon submission and state updates")
    parser.add_argument("--status", action="store_true",
                        help="Print current sync status and exit")
    parser.add_argument("--skip-scrape", action="store_true",
                        help="Skip Shiprocket data gathering, only submit pending to Amazon")
    parser.add_argument("--amazon", action="store_true",
                        help="Process 'Amazon' orders (Phase 0-3: assign, fetch AWBs, sync to SP-API, print)")
    parser.add_argument("--check-new", action="store_true",
                        help="Just check for NEW orders on Shiprocket (no action)")
    parser.add_argument("--print-order", type=str, metavar="ID",
                        help="Force download and print label for a specific Amazon Order ID, Shiprocket ID, or AWB")
    parser.add_argument("--liriya", action="store_true",
                        help="Process ONLY 'LIRIYA' orders (assign, download, print). Skips Amazon sync.")
    parser.add_argument("--orders", type=str,
                        help="Comma-separated list of order IDs to process")
    args = parser.parse_args()
    
    args.orders_list = args.orders.split(",") if args.orders else None

    if args.liriya:
        args.amazon = True
        args.skip_scrape = True

    # ── Load state ───────────────────────────────────────────────
    state = load_state(STATE_FILE)

    if args.status:
        print_status(state)
        sys.exit(0)

    # --- CHECK-NEW MODE ---
    if args.check_new:
        print("\n" + "═" * 60)
        print("  Checking for LIVE orders on Amazon & Shiprocket...")
        print("═" * 60)
        
        # 1. Check Amazon Seller Central
        amz_orders = fetch_amazon_new_orders()
        
        # 2. Check Shiprocket
        channel_name = "LIRIYA (WOOCOMMERCE)" if args.liriya else "LIRIYA (AMAZON)"
        sr_orders = check_new_orders(SHIPROCKET_EMAIL, SHIPROCKET_PASSWORD, channel_name)
        
        # --- DISPLAY AMAZON ---
        print("\n" + "─" * 40)
        print("  📊 1. AMAZON SELLER CENTRAL (Live)")
        print("─" * 40)
        if not amz_orders:
            print("  ✅ No unshipped orders currently on Amazon.")
        else:
            print(f"  📦 Found {len(amz_orders)} unshipped orders:")
            for i, order in enumerate(amz_orders, 1):
                print(f"    {i}. ID: {order['amazon_order_id']} | "
                      f"Status: {order['status']} | "
                      f"Items: {order['items_unshipped']}")

        # --- DISPLAY SHIPROCKET ---
        print("\n" + "─" * 40)
        print("  📊 2. SHIPROCKET (Pushed & Pending)")
        print("─" * 40)
        if not sr_orders:
            print("  ✅ No 'NEW' Amazon orders found on Shiprocket.")
        else:
            print(f"  📦 Found {len(sr_orders)} 'NEW' orders on Shiprocket:")
            for i, order in enumerate(sr_orders, 1):
                amz_id = order.get("channel_order_id", "Unknown")
                sr_id = order.get("id", "Unknown")
                print(f"    {i}. AMZ#{amz_id} (SR#{sr_id}) | "
                      f"Customer: {order.get('customer_name')}")

        print("\n" + "=" * 60)
        print("  💡 Tip: If Amazon has more orders than Shiprocket, run:")
        print("  'SHIPROCKET_AUTO_IMPORT=true python3 sync_awb.py --amazon'")
        print("=" * 60)
        
        print("\n🏁 Check complete.")
        return

    # --- PRINT SPECIFIC ORDER MODE ---
    if args.print_order:
        print("\n" + "═" * 60)
        print(f"  PRINT SPECIFIC ORDER MODE: {args.print_order}")
        print("═" * 60)
        
        target_id = args.print_order
        found = None
        for o in state["orders"]:
            if target_id in (o.get("amazon_order_id"), o.get("shiprocket_order_id"), o.get("awb_number")):
                found = o
                break
                
        if not found:
            logger.error(f"❌ Could not find an order in sync_state.json matching ID: {target_id}")
            logger.info("Make sure the order has been picked up by a previous sync run.")
            return
            
        amazon_id = found["amazon_order_id"]
        sr_id = found["shiprocket_order_id"]
        logger.info(f"📄 Found order - AMZ: {amazon_id} | SR: {sr_id}")
        
        from shiprocket_api import download_label_for_specific_order
        success = download_label_for_specific_order(SHIPROCKET_EMAIL, SHIPROCKET_PASSWORD, sr_id, amazon_id, args.dry_run)
        
        if success:
            # Force the state to unprinted so Phase 3 picks it up
            found["label_printed"] = False
            if not args.dry_run:
                save_state(state, STATE_FILE)
        else:
            logger.error("❌ Label download failed, aborting print phase.")
            return
            
        args.skip_scrape = True
        args.amazon = False

    # ── Validate credentials ─────────────────────────────────────
    missing = []
    if not SHIPROCKET_EMAIL: missing.append("SHIPROCKET_EMAIL")
    if not SHIPROCKET_PASSWORD: missing.append("SHIPROCKET_PASSWORD")
    if not os.getenv("AMAZON_CLIENT_ID"): missing.append("AMAZON_CLIENT_ID")
    if not os.getenv("AMAZON_CLIENT_SECRET"): missing.append("AMAZON_CLIENT_SECRET")
    if not os.getenv("AMAZON_REFRESH_TOKEN"): missing.append("AMAZON_REFRESH_TOKEN")

    if missing:
        logger.error(f"❌ Missing environment variables: {', '.join(missing)}")
        logger.error("   Please check your .env file.")
        sys.exit(1)

    logger.info("🚀 AWB Sync – Starting (100% API Mode)")
    logger.info(f"Mode      : {'DRY RUN' if args.dry_run else 'FULL SYNC'}")
    logger.info(f"State file: {os.path.abspath(STATE_FILE)}")
    logger.info(f"Time      : {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")

    # ═══════════════════════════════════════════════════════════════
    #  PHASE 0 – Courier Assignment & Pickup
    # ═══════════════════════════════════════════════════════════════
    if args.amazon and (not args.skip_scrape or args.liriya):
        print("\n" + "─" * 60)
        channel_to_process = "LIRIYA (WOOCOMMERCE)" if args.liriya else "LIRIYA (AMAZON)"
        logger.info(f"PHASE 0: Auto-Assign Couriers & Schedule Pickup ({channel_to_process})")
        print("─" * 60)
        
        success = auto_assign_couriers(
            email=SHIPROCKET_EMAIL,
            password=SHIPROCKET_PASSWORD,
            channel_name=channel_to_process,
            dry_run=args.dry_run,
            target_order_ids=args.orders_list
        )
        if not success:
            logger.warning("⚠️  Courier assignment phase encountered issues.")

    # ═══════════════════════════════════════════════════════════════
    #  PHASE 1 – Shiprocket API
    # ═══════════════════════════════════════════════════════════════
    if not args.skip_scrape:
        print("\n" + "─" * 60)
        logger.info("PHASE 1: Fetch Shiprocket Data (API)")
        print("─" * 60)

        orders = scrape_shiprocket_api(
            email=SHIPROCKET_EMAIL,
            password=SHIPROCKET_PASSWORD,
        )

        added = merge_orders(state, orders)
        
        # We only save state during dry run in PHASE 1 because it's harmless
        # and helps track discovered orders. 
        # But for Phase 2 (Sync), we strictly block state saving in dry-run.
        save_state(state, STATE_FILE)
        logger.info(f"📊 Retrieved {len(orders)} orders, {added} newly added to state.")
    else:
        logger.info("⏭️  Skipping Shiprocket API fetch (--skip-scrape)")

    # ═══════════════════════════════════════════════════════════════
    #  PHASE 2 – Amazon SP-API
    # ═══════════════════════════════════════════════════════════════
    if not args.liriya:
        pending = get_pending_orders(state)
        
        if args.orders_list:
            pending = [o for o in pending if str(o.get("amazon_order_id", "")) in args.orders_list or str(o.get("shiprocket_order_id", "")) in args.orders_list]
            
        if args.print_order:
            pending = []  # Skip amazon sync when just printing one order
    
        print("\n" + "─" * 60)
        logger.info("PHASE 2: Submit Tracking to Amazon (SP-API)")
        print("─" * 60)
        logger.info(f"Pending orders to sync: {len(pending)}")
    
        if not pending:
            logger.info("✅ Nothing to sync!")
        else:
            logger.info(f"🚀 Starting Amazon SP-API submitter for {len(pending)} order(s)...")
            results = {}
            for i, order in enumerate(pending, 1):
                sid = order["shiprocket_order_id"]
                aid = order["amazon_order_id"]
                logger.info(f"[{i}/{len(pending)}] Processing SR#{sid} → AMZ#{aid}")
                
                res = confirm_shipment_api(
                    amazon_order_id=aid,
                    courier_name=order["courier_name"],
                    awb_number=order["awb_number"],
                    dry_run=args.dry_run
                )
                results[sid] = res
    
            # Update state and print results
            for sid, result in results.items():
                if result == "SUCCESS":
                    if not args.dry_run:
                        mark_synced(state, sid)
                        logger.info(f"✅ SR#{sid} synced successfully")
                    else:
                        logger.info(f"✨ [DRY RUN] SR#{sid} would be marked as synced")
                else:
                    mark_error(state, sid, result)
                    logger.error(f"❌ SR#{sid} failed: {result}")
    
            # Final state save (Production only)
            if not args.dry_run:
                save_state(state, STATE_FILE)
            else:
                logger.info("ℹ️  Dry run: State file was NOT updated for Phase 2.")

    # ═══════════════════════════════════════════════════════════════
    #  DONE
    # ═══════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════
    #  PHASE 3 – PDF Automation (NEW)
    # ═══════════════════════════════════════════════════════════════
    date_str = datetime.now().strftime("%d-%m-%Y")
    label_folder = os.path.join("labels", date_str)
    
    if os.path.exists(label_folder):
        print("\n" + "─" * 60)
        logger.info("PHASE 3: PDF to Image Conversion (Automated)")
        print("─" * 60)
        image_paths = pdf_tool.process_folder(label_folder)
        if image_paths:
            logger.info(f"🖨️  Checking {len(image_paths)} images for printing...")
            printed_count = 0
            for img_path in image_paths:
                # Get amazon_order_id from filename (order_id.png or label_order_id.png)
                filename = os.path.basename(img_path).replace(".png", "")
                amazon_order_id = filename.replace("label_", "")
                
                # If we are in print-order mode, ONLY print the requested order
                if args.print_order and amazon_order_id != found["amazon_order_id"]:
                    continue
                    
                if args.orders_list and amazon_order_id not in args.orders_list:
                    continue
                
                # Check if already printed
                order_in_state = next((o for o in state["orders"] if o.get("amazon_order_id") == amazon_order_id), None)
                if order_in_state and order_in_state.get("label_printed"):
                    logger.info(f"⏩ Skipping Order#{amazon_order_id} (already printed)")
                    continue
                
                if args.dry_run:
                    logger.info(f"✨ [DRY RUN] Would print Order#{amazon_order_id}")
                    if order_in_state:
                        pass # State not saved in dry run for Phase 3
                    printed_count += 1
                    continue

                if print_image(img_path, size="A6"):
                    if order_in_state:
                        mark_printed(state, amazon_order_id)
                    elif args.liriya:
                        state["orders"].append({
                            "shiprocket_order_id": f"LIRIYA_{amazon_order_id}",
                            "amazon_order_id": amazon_order_id,
                            "synced_to_amazon": True,
                            "label_printed": True,
                            "printed_at": datetime.now(IST).isoformat(),
                        })
                    printed_count += 1
                    
                    # Move files to printed/ directory
                    printed_dir = os.path.join(label_folder, "printed")
                    os.makedirs(printed_dir, exist_ok=True)
                    try:
                        import shutil
                        # Move PNG
                        shutil.move(img_path, os.path.join(printed_dir, os.path.basename(img_path)))
                        # Move PDF
                        pdf_path = img_path.replace(".png", ".pdf")
                        if os.path.exists(pdf_path):
                            shutil.move(pdf_path, os.path.join(printed_dir, os.path.basename(pdf_path)))
                    except Exception as e:
                        logger.warning(f"⚠️ Could not move files for {amazon_order_id}: {e}")
            
            if printed_count > 0 and not args.dry_run:
                save_state(state, STATE_FILE)
                logger.info(f"✅ Sent {printed_count} new labels to printer.")
            elif printed_count == 0:
                logger.info("✅ All labels already printed.")
        else:
            logger.info("ℹ️  No images found or processed for printing.")
    
    print("\n" + "─" * 60)
    print_status(state)
    logger.info("🏁 Sync complete.")


if __name__ == "__main__":
    main()
