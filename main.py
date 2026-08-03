#!/usr/bin/env python3
"""
main.py — Resume Parser entry point.

Usage:
    python main.py                          # process ./input_resumes/
    python main.py /path/to/pdf/folder      # process a custom folder
    python main.py /path/to/single.pdf      # process a single file

Results are written to ./output/ as JSON, CSV, and XLSX.
"""

import sys
import time
import logging
from pathlib import Path

from config import INPUT_DIR, OUTPUT_DIR
from extractor import extract_text
from parser import parse_resume

from exporter import export_all
from schemas import CandidateRecord

# ── Logging setup ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("resume_parser")


def collect_pdfs(source: Path) -> list[Path]:
    """Return a sorted list of PDF files from the given path."""
    if source.is_file() and source.suffix.lower() == ".pdf":
        return [source]
    if source.is_dir():
        pdfs = sorted(source.glob("*.pdf"))
        if not pdfs:
            logger.warning("No PDF files found in %s", source)
        return pdfs
    logger.error("Source is neither a PDF file nor a directory: %s", source)
    return []


def process_pdf(pdf_path: Path) -> CandidateRecord:
    """Extract text from one PDF and parse it into a CandidateRecord."""
    logger.info("─── Processing: %s", pdf_path.name)
    raw_text, method = extract_text(str(pdf_path))
    logger.info("    Extraction method: %s  |  Characters: %d", method, len(raw_text))
    record = parse_resume(raw_text, source_file=pdf_path.name)
    record.field_confidence["extraction_method"] = method
    logger.info(
        "    Parsed → name=%s | email=%s | skills=%d | experience=%d entries",
        record.full_name or "(not found)",
        record.email or "(not found)",
        len(record.skills),
        len(record.work_experience),
    )
    return record


def main():
    # Determine source folder/file
    if len(sys.argv) > 1:
        source = Path(sys.argv[1])
    else:
        source = INPUT_DIR

    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║       RESUME  PARSER  —  starting        ║")
    logger.info("╚══════════════════════════════════════════╝")
    logger.info("Source : %s", source.resolve())
    logger.info("Output : %s", OUTPUT_DIR.resolve())

    pdfs = collect_pdfs(source)
    if not pdfs:
        logger.error("Nothing to process. Place PDF resumes in %s", INPUT_DIR)
        sys.exit(1)

    logger.info("Found %d PDF(s) to process.\n", len(pdfs))

    t0 = time.perf_counter()
    records: list[CandidateRecord] = []

    for pdf in pdfs:
        try:
            rec = process_pdf(pdf)
            records.append(rec)
        except Exception as exc:
            logger.error("FAILED on %s: %s", pdf.name, exc, exc_info=True)

    elapsed = time.perf_counter() - t0
    logger.info("\nParsed %d / %d files in %.1f s", len(records), len(pdfs), elapsed)

    if records:
        paths = export_all(records)
        logger.info("─── Exports ───")
        for fmt, p in paths.items():
            logger.info("  %s → %s", fmt.upper(), p)

    logger.info("Done.")


if __name__ == "__main__":
    main()
