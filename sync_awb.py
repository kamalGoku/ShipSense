import argparse
import os
import shutil
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

# ── Load environment early ───────────────────────────────────────────
load_dotenv()

import config
from state_manager import (
    load_state, save_state, merge_orders,
    get_pending_orders, mark_synced, mark_error,
    mark_printed,
)
from shiprocket_api import (
    fetch_shiprocket_orders, auto_assign_couriers,
    check_new_orders, download_label_for_specific_order,
)
from amazon_api import confirm_shipment_api, fetch_amazon_new_orders
import pdf_tool
from print_tool import print_image
from logger import get_logger

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

SHIPROCKET_EMAIL    = os.getenv("SHIPROCKET_EMAIL", "")
SHIPROCKET_PASSWORD = os.getenv("SHIPROCKET_PASSWORD", "")
STATE_FILE          = config.STATE_FILE_PATH
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


def print_orders_table(orders: list[dict]) -> None:
    """Print the fetched-orders CLI table (moved here from shiprocket_api)."""
    for i, o in enumerate(orders):
        print(f"     {i+1}. SR#{o['shiprocket_order_id']} ({o.get('_status', '')}) "
              f"→ AMZ#{o['amazon_order_id']} | {o.get('courier_name') or 'No Courier'} "
              f"| AWB: {o.get('awb_number') or 'No AWB'}")


def run_check_new(args) -> None:
    """--check-new mode: report LIVE orders on Amazon & Shiprocket, no action."""
    print("\n" + "═" * 60)
    print("  Checking for LIVE orders on Amazon & Shiprocket...")
    print("═" * 60)

    # 1. Check Amazon Seller Central (raises on failure)
    amz_orders = fetch_amazon_new_orders()

    # 2. Check Shiprocket
    sr_orders = check_new_orders(SHIPROCKET_EMAIL, SHIPROCKET_PASSWORD, args.channel)

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
            status = order.get("status", "Unknown")
            print(f"    {i}. AMZ#{amz_id} (SR#{sr_id}) | Status: {status}")

    print("\n" + "=" * 60)
    print("  💡 Tip: If Amazon has more orders than Shiprocket, run:")
    print("  'SHIPROCKET_AUTO_IMPORT=true python3 sync_awb.py --amazon'")
    print("=" * 60)

    print("\n🏁 Check complete.")


def prepare_print_order(state: dict, args) -> dict | None:
    """--print-order mode: locate the order, download its label and flag it
    for reprint. Returns the matching state entry, or None if not found /
    download failed (caller should stop)."""
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
        return None

    amazon_id = found["amazon_order_id"]
    sr_id = found["shiprocket_order_id"]
    logger.info(f"📄 Found order - AMZ: {amazon_id} | SR: {sr_id}")

    success = download_label_for_specific_order(
        SHIPROCKET_EMAIL, SHIPROCKET_PASSWORD, sr_id, amazon_id, args.dry_run
    )

    if not success:
        logger.error("❌ Label download failed, aborting print phase.")
        return None

    # Force the state to unprinted so the print phase picks it up
    found["label_printed"] = False
    if not args.dry_run:
        save_state(state, STATE_FILE)
    return found


# ═══════════════════════════════════════════════════════════════════
#  PHASE 0 – Courier Assignment & Pickup
# ═══════════════════════════════════════════════════════════════════
def phase_assign_couriers(args) -> None:
    print("\n" + "─" * 60)
    logger.info(f"PHASE 0: Auto-Assign Couriers & Schedule Pickup ({args.channel})")
    print("─" * 60)

    success = auto_assign_couriers(
        email=SHIPROCKET_EMAIL,
        password=SHIPROCKET_PASSWORD,
        channel_name=args.channel,
        dry_run=args.dry_run,
        target_order_ids=args.orders_list,
    )
    if not success:
        logger.warning("⚠️  Courier assignment phase encountered issues.")


# ═══════════════════════════════════════════════════════════════════
#  PHASE 1 – Shiprocket API
# ═══════════════════════════════════════════════════════════════════
def phase_fetch_awbs(state: dict, args) -> None:
    print("\n" + "─" * 60)
    logger.info("PHASE 1: Fetch Shiprocket Data (API)")
    print("─" * 60)

    # fetch_shiprocket_orders raises on failure; the phase wrapper in main()
    # turns that into a clear log + nonzero exit.
    orders = fetch_shiprocket_orders(
        email=SHIPROCKET_EMAIL,
        password=SHIPROCKET_PASSWORD,
    )
    print_orders_table(orders)

    added = merge_orders(state, orders)

    # We save state even during dry run in PHASE 1 because it's harmless
    # and helps track discovered orders.
    # But for Phase 2 (Sync), we strictly block state saving in dry-run.
    save_state(state, STATE_FILE)
    logger.info(f"📊 Retrieved {len(orders)} orders, {added} newly added to state.")


# ═══════════════════════════════════════════════════════════════════
#  PHASE 2 – Amazon SP-API
# ═══════════════════════════════════════════════════════════════════
def phase_confirm_amazon(state: dict, args) -> None:
    pending = get_pending_orders(state)

    if args.orders_list:
        pending = [o for o in pending
                   if str(o.get("amazon_order_id", "")) in args.orders_list
                   or str(o.get("shiprocket_order_id", "")) in args.orders_list]

    if args.print_order:
        pending = []  # Skip amazon sync when just printing one order

    print("\n" + "─" * 60)
    logger.info("PHASE 2: Submit Tracking to Amazon (SP-API)")
    print("─" * 60)
    logger.info(f"Pending orders to sync: {len(pending)}")

    if not pending:
        logger.info("✅ Nothing to sync!")
        return

    logger.info(f"🚀 Starting Amazon SP-API submitter for {len(pending)} order(s)...")
    for i, order in enumerate(pending, 1):
        sid = order["shiprocket_order_id"]
        aid = order["amazon_order_id"]
        logger.info(f"[{i}/{len(pending)}] Processing SR#{sid} → AMZ#{aid}")

        result = confirm_shipment_api(
            amazon_order_id=aid,
            courier_name=order["courier_name"],
            awb_number=order["awb_number"],
            dry_run=args.dry_run,
        )

        if result == "SUCCESS":
            if not args.dry_run:
                mark_synced(state, sid)
                # Persist immediately: save_state is atomic and cheap, and a
                # per-order save closes the crash window in which an already
                # confirmed shipment would be re-confirmed on the next run.
                save_state(state, STATE_FILE)
                logger.info(f"✅ SR#{sid} synced successfully")
            else:
                logger.info(f"✨ [DRY RUN] SR#{sid} would be marked as synced")
        else:
            mark_error(state, sid, result)
            if not args.dry_run:
                save_state(state, STATE_FILE)
            logger.error(f"❌ SR#{sid} failed: {result}")

    if args.dry_run:
        logger.info("ℹ️  Dry run: State file was NOT updated for Phase 2.")


# ═══════════════════════════════════════════════════════════════════
#  PHASE 3 – PDF Automation (labels → PNG → printer)
# ═══════════════════════════════════════════════════════════════════
def _collect_label_folders() -> list[str]:
    """Return all date folders under labels/ that may hold un-archived labels.

    Scanning every folder (not just today's DD-MM-YYYY folder) fixes the
    midnight boundary: labels downloaded before midnight are still printed
    and archived by a run that starts after midnight.
    """
    labels_root = config.LABELS_DIR
    if not os.path.isdir(labels_root):
        return []
    folders = []
    for entry in sorted(os.listdir(labels_root)):
        folder = os.path.join(labels_root, entry)
        if os.path.isdir(folder) and entry != "printed":
            folders.append(folder)
    return folders


def phase_print_labels(state: dict, args, print_order_target: dict | None = None) -> None:
    label_folders = _collect_label_folders()
    if not label_folders:
        return

    print("\n" + "─" * 60)
    logger.info("PHASE 3: PDF to Image Conversion (Automated)")
    print("─" * 60)

    image_paths = []
    for label_folder in label_folders:
        folder_images = pdf_tool.process_folder(label_folder)
        if folder_images:
            image_paths.extend(folder_images)

    if not image_paths:
        logger.info("ℹ️  No images found or processed for printing.")
        return

    logger.info(f"🖨️  Checking {len(image_paths)} images for printing...")
    printed_count = 0
    for img_path in image_paths:
        # Get amazon_order_id from filename (order_id.png or label_order_id.png)
        filename = os.path.basename(img_path).replace(".png", "")
        amazon_order_id = filename.replace("label_", "")

        # If we are in print-order mode, ONLY print the requested order
        if print_order_target and amazon_order_id != print_order_target["amazon_order_id"]:
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
            printed_count += 1
            continue

        if print_image(img_path, size="A6"):
            if order_in_state:
                mark_printed(state, amazon_order_id)
            elif args.liriya:
                state["orders"].append({
                    "shiprocket_order_id": f"{config.WOO_STATE_PREFIX}{amazon_order_id}",
                    "amazon_order_id": amazon_order_id,
                    "synced_to_amazon": True,
                    "label_printed": True,
                    "printed_at": datetime.now(IST).isoformat(),
                })
            printed_count += 1

            # Move files to the printed/ directory inside the label's folder
            printed_dir = os.path.join(os.path.dirname(img_path), "printed")
            os.makedirs(printed_dir, exist_ok=True)
            try:
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


def build_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--channel", type=str, default=None, metavar="NAME",
                        help=f"Shiprocket channel to process (preferred over --liriya). "
                             f"Default: '{config.AMAZON_CHANNEL_NAME}'")
    parser.add_argument("--liriya", action="store_true",
                        help=f"Process ONLY '{config.WOO_CHANNEL_NAME}' orders "
                             f"(assign, download, print). Skips Amazon sync. "
                             f"Legacy alias for --channel '{config.WOO_CHANNEL_NAME}'.")
    parser.add_argument("--orders", type=str,
                        help="Comma-separated list of order IDs to process")
    return parser


def _run_phase(name: str, fn, *fn_args, **fn_kwargs):
    """Run one phase; on failure log clearly and exit nonzero.

    Each phase persists its own state incrementally, so aborting here never
    leaves the state file half-written."""
    try:
        return fn(*fn_args, **fn_kwargs)
    except SystemExit:
        raise
    except Exception:
        logger.exception(f"❌ {name} failed — aborting sync.")
        sys.exit(1)


def main():
    args = build_parser().parse_args()

    args.orders_list = (
        [oid.strip() for oid in args.orders.split(",") if oid.strip()]
        if args.orders else None
    )

    # Resolve the channel: --channel wins, --liriya maps to the WooCommerce
    # channel, otherwise the configured Amazon channel.
    if args.channel:
        if args.channel == config.WOO_CHANNEL_NAME:
            args.liriya = True
    else:
        args.channel = config.WOO_CHANNEL_NAME if args.liriya else config.AMAZON_CHANNEL_NAME

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
        _run_phase("CHECK-NEW", run_check_new, args)
        return

    # --- PRINT SPECIFIC ORDER MODE ---
    print_order_target = None
    if args.print_order:
        print_order_target = _run_phase("PRINT-ORDER preparation", prepare_print_order, state, args)
        if print_order_target is None:
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

    # ── Phase dispatch ───────────────────────────────────────────
    if args.amazon and (not args.skip_scrape or args.liriya):
        _run_phase("PHASE 0 (courier assignment)", phase_assign_couriers, args)

    if not args.skip_scrape:
        _run_phase("PHASE 1 (Shiprocket fetch)", phase_fetch_awbs, state, args)
    else:
        logger.info("⏭️  Skipping Shiprocket API fetch (--skip-scrape)")

    if not args.liriya:
        _run_phase("PHASE 2 (Amazon confirm)", phase_confirm_amazon, state, args)

    _run_phase("PHASE 3 (label printing)", phase_print_labels, state, args, print_order_target)

    print("\n" + "─" * 60)
    print_status(state)
    logger.info("🏁 Sync complete.")


if __name__ == "__main__":
    main()
