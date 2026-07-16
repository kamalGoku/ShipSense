import subprocess
import os
import sys
from logger import get_logger

logger = get_logger(__name__)

def list_printers():
    """List available printers on the system."""
    try:
        result = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, check=True)
        logger.info("🖨️ Available Printers:")
        logger.info(result.stdout)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Error listing printers: {e}")

PRINTER_MAC = "e0:bb:9e:83:1a:02"

def discover_printer_ip():
    """Finds the printer's IP address on the network using its MAC address."""
    logger.info(f"🔍 Searching for printer with MAC {PRINTER_MAC}...")
    try:
        # 1. Refresh ARP table by pinging the subnet
        # We assume the user is on 192.168.29.x based on previous research
        subprocess.run(["fping", "-a", "-g", "192.168.29.0/24", "-q"], capture_output=True)
        
        # 2. Check ARP table
        result = subprocess.run(["arp", "-an"], capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            if PRINTER_MAC in line.lower():
                # Example: ? (192.168.29.105) at e0:bb:9e:83:1a:02 on en0 ifscope [ethernet]
                parts = line.split()
                if len(parts) > 1:
                    ip = parts[1].strip("()")
                    logger.info(f"✨ Found printer at {ip}")
                    return ip
    except Exception as e:
        logger.warning(f"⚠️ Discovery error: {e}")
    return None

def update_system_printer_uri(printer_name, new_ip):
    """Updates the CUPS device URI for the printer."""
    new_uri = f"ipp://{new_ip}/ipp/print"
    logger.info(f"⚙️ Updating printer '{printer_name}' to URI: {new_uri}")
    try:
        subprocess.run(["lpadmin", "-p", printer_name, "-v", new_uri], check=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Failed to update printer URI: {e}")
        return False

def check_printer_status(printer_name):
    """Checks if the printer is reachable and accepting jobs. Auto-discovers if offline."""
    try:
        if not printer_name:
            # Get default printer if none specified
            res = subprocess.run(["lpstat", "-d"], capture_output=True, text=True, check=True)
            printer_name = res.stdout.split(":")[-1].strip()
        
        # 1. Proactively check if the configured IP is reachable
        uri_res = subprocess.run(["lpstat", "-v", printer_name], capture_output=True, text=True, check=True)
        # device for EPSON_L3250_Series: ipp://192.168.29.105/ipp/print
        current_ip = None
        if "ipp://" in uri_res.stdout:
            current_ip = uri_res.stdout.split("ipp://")[1].split("/")[0]
        
        needs_discovery = False
        if current_ip:
            ping_res = subprocess.run(["ping", "-c", "1", "-W", "1", current_ip], capture_output=True)
            if ping_res.returncode != 0:
                logger.warning(f"📡 IP {current_ip} is unreachable.")
                needs_discovery = True

        # 2. Check CUPS status
        result = subprocess.run(["lpstat", "-p", printer_name], capture_output=True, text=True, check=True)
        status_output = result.stdout.strip()
        
        if "Looking for printer" in status_output or "is not responding" in status_output:
            needs_discovery = True

        if needs_discovery:
            logger.warning(f"⚠️ Printer '{printer_name}' appears to be disconnected or moved.")
            new_ip = discover_printer_ip()
            if new_ip and new_ip != current_ip:
                if update_system_printer_uri(printer_name, new_ip):
                    # Recheck status after update
                    result = subprocess.run(["lpstat", "-p", printer_name], capture_output=True, text=True, check=True)
                    return True, printer_name, result.stdout.strip()
            elif new_ip == current_ip:
                logger.info("ℹ️ Printer is at the same IP but still not responding. Please check hardware.")
        
        if "is idle" in status_output or "is processing" in status_output:
            return True, printer_name, status_output
        return False, printer_name, status_output
    except subprocess.CalledProcessError as e:
        return False, printer_name, e.stderr.strip()

def print_image(file_path, printer_name=None, size="A4", color="RGB", fit=True):
    """
    Prints an image using the macOS 'lp' command.
    """
    if not os.path.exists(file_path):
        logger.error(f"❌ Error: File '{file_path}' not found.")
        return False

    # Check printer status first
    is_ready, actual_printer, status_msg = check_printer_status(printer_name)
    if not is_ready:
        logger.warning(f"⚠️ Warning: Printer '{actual_printer}' might not be ready.")
        logger.warning(f"   Status: {status_msg}")
        # We proceed anyway but warn the user

    cmd = ["lp"]
    if printer_name:
        cmd += ["-d", printer_name]
    
    # Paper size handling
    if "x" in size and size.lower().endswith("mm"):
        try:
            dims = size.lower().replace("mm", "").split("x")
            width_pts = float(dims[0]) * 2.8346
            height_pts = float(dims[1]) * 2.8346
            size = f"Custom.{width_pts:.2f}x{height_pts:.2f}"
        except ValueError:
            pass

    cmd += ["-o", f"PageSize={size}"]
    cmd += ["-o", f"ColorModel={color}"]
    if fit:
        cmd += ["-o", "fit-to-page"]
    
    cmd.append(file_path)
    
    logger.info(f"📡 Sending '{os.path.basename(file_path)}' to printer...")
    logger.info(f"   Settings: Size={size}, Color={color}, Fit={fit}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"✅ Print job submitted! {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Printing failed Error:\n{e.stderr}")
        return False

if __name__ == "__main__":
    # Simple CLI for discovery
    if len(sys.argv) == 1:
        print("Usage:")
        print("  python print_tool.py list              # List all printers")
        print("  python print_tool.py options [name]    # List options for a printer")
        print("  python print_tool.py print [file] [opt:size] [opt:printer]")
        sys.exit(0)

    action = sys.argv[1].lower()
    
    if action == "list":
        list_printers()
    elif action == "options" and len(sys.argv) > 2:
        get_printer_options(sys.argv[2])
    elif action == "print" and len(sys.argv) > 2:
        file = sys.argv[2]
        paper = sys.argv[3] if len(sys.argv) > 3 else "A4"
        ptr = sys.argv[4] if len(sys.argv) > 4 else None
        print_image(file, printer_name=ptr, size=paper)
    else:
        print("Invalid command.")
