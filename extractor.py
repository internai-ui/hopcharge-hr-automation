"""
extractor.py — Extracts raw text from PDFs.

Strategy:
  1. Try pdfplumber (layout-aware, good for most text-based PDFs).
  2. If yield is too low, fall back to PyMuPDF (fitz).
  3. If still too low, rasterise each page and run Tesseract OCR.

Returns the best text it can get plus a flag indicating the method used.
"""

import logging
from pathlib import Path

import pdfplumber
import fitz  # PyMuPDF

from config import MIN_TEXT_LENGTH, OCR_DPI, TESSERACT_LANG

logger = logging.getLogger(__name__)


def _text_via_pdfplumber(pdf_path: str) -> str:
    """Extract text using pdfplumber (preserves layout well)."""
    pages_text = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                txt = page.extract_text()
                if txt:
                    pages_text.append(txt)
    except Exception as exc:
        logger.warning("pdfplumber failed on %s: %s", pdf_path, exc)
    return "\n".join(pages_text)


def _text_via_pymupdf(pdf_path: str) -> str:
    """Extract text using PyMuPDF — fast and handles many edge-cases."""
    pages_text = []
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            pages_text.append(page.get_text())
        doc.close()
    except Exception as exc:
        logger.warning("PyMuPDF failed on %s: %s", pdf_path, exc)
    return "\n".join(pages_text)


def _text_via_ocr(pdf_path: str) -> str:
    """Rasterise every page and run Tesseract OCR (slow but handles scans)."""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        logger.error("pytesseract or Pillow not installed — OCR unavailable.")
        return ""

    pages_text = []
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=OCR_DPI)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(img, lang=TESSERACT_LANG)
            if text.strip():
                pages_text.append(text)
        doc.close()
    except Exception as exc:
        logger.warning("OCR failed on %s: %s", pdf_path, exc)
    return "\n".join(pages_text)


def extract_text(pdf_path: str) -> tuple[str, str]:
    """
    Return (extracted_text, method_used).

    method_used is one of: 'pdfplumber', 'pymupdf', 'ocr', 'empty'.
    """
    path = Path(pdf_path)
    if not path.exists():
        logger.error("File not found: %s", pdf_path)
        return "", "empty"

    # Strategy 1 — pdfplumber
    text = _text_via_pdfplumber(pdf_path)
    if len(text.strip()) >= MIN_TEXT_LENGTH:
        logger.info("%s: extracted via pdfplumber (%d chars)", path.name, len(text))
        return text, "pdfplumber"

    # Strategy 2 — PyMuPDF
    text = _text_via_pymupdf(pdf_path)
    if len(text.strip()) >= MIN_TEXT_LENGTH:
        logger.info("%s: extracted via PyMuPDF (%d chars)", path.name, len(text))
        return text, "pymupdf"

    # Strategy 3 — OCR
    logger.info("%s: text extraction weak, falling back to OCR…", path.name)
    text = _text_via_ocr(pdf_path)
    if len(text.strip()) >= MIN_TEXT_LENGTH:
        logger.info("%s: extracted via OCR (%d chars)", path.name, len(text))
        return text, "ocr"

    logger.warning("%s: all extraction methods returned minimal text.", path.name)
    return text, "empty"
