import fitz  # PyMuPDF
import sys
import os
from logger import get_logger

logger = get_logger(__name__)

def crop_pdf(input_path, output_path, x0, y0, x1, y1, page_num=0):
    """
    Crops a PDF page to the specified coordinates.
    Coordinates are in points (1/72 inch).
    """
    if not os.path.exists(input_path):
        logger.error(f"Error: File '{input_path}' not found.")
        return False

    doc = fitz.open(input_path)
    if page_num >= len(doc):
        logger.error(f"Error: Page {page_num} not found in PDF.")
        return False

    page = doc[page_num]
    # Define the rectangle (x0, y0, x1, y1)
    rect = fitz.Rect(x0, y0, x1, y1)
    
    # Set the cropbox
    page.set_cropbox(rect)
    
    # Save the result
    doc.save(output_path)
    doc.close()
    logger.info(f"✅ Cropped PDF saved to: {output_path}")
    return True

def extract_image(input_path, output_path, x0, y0, x1, y1, page_num=0, dpi=300):
    """
    Extracts a specific area of a PDF page as an image.
    """
    if not os.path.exists(input_path):
        logger.error(f"Error: File '{input_path}' not found.")
        return False

    doc = fitz.open(input_path)
    if page_num >= len(doc):
        logger.error(f"Error: Page {page_num} not found in PDF.")
        return False

    page = doc[page_num]
    rect = fitz.Rect(x0, y0, x1, y1)
    
    # Render the specific rectangle to an image
    # Scale corresponds to DPI (72 is default)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, clip=rect)
    
    pix.save(output_path)
    doc.close()
    logger.info(f"✅ Extraction saved as image: {output_path}")
    return True

def process_folder(folder_path, x0=5, y0=5, x1=425, y1=590):
    """
    Processes all PDF files in a folder and saves them as images.
    Uses specific coordinates for cropping/screenshotting.
    """
    if not os.path.exists(folder_path):
        logger.error(f"Error: Folder '{folder_path}' not found.")
        return

    logger.info(f"📂 Processing PDFs in folder: {folder_path}...")
    pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith(".pdf")]
    
    if not pdf_files:
        logger.info("  ⚠️ No PDF files found in folder.")
        return

    processed_images = []
    for filename in pdf_files:
        in_path = os.path.join(folder_path, filename)
        out_path = os.path.join(folder_path, filename.replace(".pdf", ".png"))
        
        # Only process if image doesn't exist yet
        if not os.path.exists(out_path):
            extract_image(in_path, out_path, x0, y0, x1, y1)
        else:
            logger.info(f"  ⏩ Skipping {filename} (image already exists)")
        
        if os.path.exists(out_path):
            processed_images.append(out_path)
    
    return processed_images


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage:")
        print("  Individual: python pdf_tool.py [crop|image] [input_pdf] [output_file] [x0] [y0] [x1] [y1]")
        print("  Batch:      python pdf_tool.py batch [folder_path]")
        sys.exit(1)

    action = sys.argv[1].lower()
    
    if action == "batch":
        folder = sys.argv[2]
        process_folder(folder)
    elif len(sys.argv) >= 7:
        in_pdf = sys.argv[2]
        out_file = sys.argv[3]
        coords = [float(x) for x in sys.argv[4:8]]
        page = int(sys.argv[8]) if len(sys.argv) > 8 else 0

        if action == "crop":
            crop_pdf(in_pdf, out_file, *coords, page_num=page)
        elif action == "image":
            extract_image(in_pdf, out_file, *coords, page_num=page)
    else:
        print("Invalid syntax.")
