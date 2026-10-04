import os
import logging
from typing import Optional, Tuple
import pypdfium2 as pdfium
from PIL import Image

logger = logging.getLogger(__name__)


def parse_grobid_coords(coords_str: str) -> Optional[Tuple[int, float, float, float, float]]:
    """
    Parses Grobid coords string: "page,x,y,w,h" (e.g. "4,120.5,340.2,450.0,300.0").
    If multiple bounding boxes are separated by semicolons, returns the first one.
    Page is 1-indexed in Grobid TEI XML.
    """
    if not coords_str or not isinstance(coords_str, str):
        return None

    first_box = coords_str.strip().split(";")[0].strip()
    parts = first_box.split(",")
    if len(parts) < 5:
        return None

    try:
        page = int(parts[0])
        x = float(parts[1])
        y = float(parts[2])
        w = float(parts[3])
        h = float(parts[4])
        return page, x, y, w, h
    except (ValueError, TypeError) as e:
        logger.warning(f"Failed to parse Grobid coordinates '{coords_str}': {e}")
        return None


def crop_pdf_figure(
    pdf_path: str,
    coords_str: str,
    output_path: str,
    dpi: int = 200,
    padding_pct: float = 0.08
) -> Optional[str]:
    """
    Renders a high-DPI raster of a figure from a PDF based on Grobid coordinates
    and saves it to output_path.

    :param pdf_path: Absolute or relative filesystem path to the PDF file.
    :param coords_str: Grobid coordinates string, e.g. "4,120.5,340.2,450.0,300.0".
    :param output_path: Destination filepath for the cropped PNG image.
    :param dpi: Target resolution in DPI (default 200).
    :param padding_pct: Relative safety padding added to each side of bounding box.
    :return: output_path if successful, None otherwise.
    """
    if not os.path.exists(pdf_path):
        logger.warning(f"Cannot crop figure: PDF path does not exist: {pdf_path}")
        return None

    coords = parse_grobid_coords(coords_str)
    if not coords:
        logger.warning(f"Cannot crop figure: Invalid coordinates: '{coords_str}'")
        return None

    page_num, x, y, w, h = coords
    if w <= 0 or h <= 0:
        logger.warning(f"Cannot crop figure: Non-positive dimensions (w={w}, h={h})")
        return None

    try:
        pdf = pdfium.PdfDocument(pdf_path)
        # Grobid page indices are 1-indexed
        page_index = page_num - 1
        if page_index < 0 or page_index >= len(pdf):
            logger.warning(f"Cannot crop figure: Page {page_num} out of bounds (total pages: {len(pdf)})")
            pdf.close()
            return None

        page = pdf[page_index]
        scale = dpi / 72.0  # Grobid PDF coords are 72 points per inch

        # Render the page to a PIL image
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()

        img_w, img_h = pil_image.size

        # Apply safety padding
        pad_x = w * padding_pct
        pad_y = h * padding_pct

        left = max(0, int((x - pad_x) * scale))
        top = max(0, int((y - pad_y) * scale))
        right = min(img_w, int((x + w + pad_x) * scale))
        bottom = min(img_h, int((y + h + pad_y) * scale))

        if right <= left or bottom <= top:
            logger.warning(f"Cannot crop figure: Invalid pixel bounding box: ({left}, {top}, {right}, {bottom})")
            pdf.close()
            return None

        cropped = pil_image.crop((left, top, right, bottom))

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        cropped.save(output_path, format="PNG")
        pdf.close()
        return output_path

    except Exception as e:
        logger.error(f"Error cropping figure from {pdf_path} at coords {coords_str}: {e}")
        return None
