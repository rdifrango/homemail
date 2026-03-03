#!/usr/bin/python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pymupdf",
#     "anthropic",
#     "Pillow",
#     "pytesseract",
# ]
# ///
"""
Mail Scanner Pipeline v2 — Raw / Organized Architecture
========================================================
Watches a "Raw" folder for raw scanned PDFs from your Epson RR-600W,
splits them at blank pages, then copies and renames them into an organized
folder structure using AI classification.

Raw layer:       Scans exactly as they came from the scanner. READ-ONLY.
                 Never modified or moved. This is your archival safety net.

Organized layer: AI-organized copies in a single flat folder. Category, sender,
                 and doc type are encoded in the filename for easy sorting/searching.

Dependencies are declared inline (PEP 723) — uv handles them automatically.

Usage:
    uv run pipeline.py                              # Watch mode + dashboard on :8080
    uv run pipeline.py --batch                      # Process existing and exit
    uv run pipeline.py --no-ai                      # Skip AI, use date-based names
    uv run pipeline.py --port 9090                  # Custom dashboard port
    uv run pipeline.py --port 0                     # Disable dashboard
    uv run pipeline.py -v                           # Verbose logging
"""

import os
import sys
import time
import json
import hashlib
import argparse
import logging
import csv
import threading
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from pathlib import Path
from datetime import datetime, date
from dataclasses import dataclass, asdict, field
from typing import Optional

import fitz  # PyMuPDF

try:
    from PIL import Image
    import pytesseract
    # Verify the actual tesseract binary is installed, not just the Python wrapper
    pytesseract.get_tesseract_version()
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False
except Exception:
    # pytesseract installed but tesseract binary missing
    HAS_TESSERACT = False

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# ============================================================
# CONFIGURATION
# ============================================================

CONFIG = {
    # ---- Folder Structure ----
    # Raw: scans land here from the scanner. NEVER modified.
    "bronze_folder": "/opt/homemail/Raw",

    # Organized: AI-classified copies with smart names
    "silver_folder": "/opt/homemail/Organized",

    # Reports: processing ledger and document index live here
    "tracking_folder": "/opt/homemail/Reports",

    # ---- Blank Page Detection ----
    "blank_threshold": 0.98,
    "blank_min_text_length": 10,

    # ---- AI Settings ----
    "use_ai_renaming": True,

    # ---- Processing ----
    "poll_interval": 15,        # seconds between folder scans
    "ocr_if_needed": True,      # OCR pages without embedded text

    # ---- Verification ----
    "verify_copies": True,      # SHA-256 verify organized copies match raw splits
}

# ---- Category Labels (used in filenames) ----
# The AI classifies each document into one of these categories.
# The "label" appears in the filename: YYYY-MM-DD_Label_Sender_DocType.pdf
# Customize freely — add, remove, or rename.

CATEGORIES = {
    "bill": {
        "label": "Bill",
        "description": "Any bill, invoice, or payment request that needs to be paid",
    },
    "financial_statement": {
        "label": "Financial-Statement",
        "description": "Bank statements, credit card statements, investment account statements",
    },
    "financial_tax": {
        "label": "Tax",
        "description": "W-2s, 1099s, tax returns, IRS letters, charitable donation receipts",
    },
    "financial_insurance": {
        "label": "Insurance",
        "description": "Insurance policies, EOBs, claims, coverage letters (health, auto, home, life)",
    },
    "financial_retirement": {
        "label": "Retirement",
        "description": "401k, IRA, brokerage statements, pension, Social Security",
    },
    "housing": {
        "label": "Housing",
        "description": "Mortgage statements, HOA, property tax, home warranty, deed, title",
    },
    "utility": {
        "label": "Utility",
        "description": "Electric, gas, water, sewer, internet, phone, cable, trash (non-bill correspondence)",
    },
    "vehicle": {
        "label": "Vehicle",
        "description": "Auto loan, registration, title, DMV, toll, parking, repair records",
    },
    "medical": {
        "label": "Medical",
        "description": "Medical records, lab results, prescriptions, doctor letters, dental, vision (not bills)",
    },
    "legal": {
        "label": "Legal",
        "description": "Contracts, court documents, attorney letters, power of attorney, wills, trusts",
    },
    "kids_school": {
        "label": "Kids-School",
        "description": "Report cards, enrollment, school letters, permission slips, transcripts",
    },
    "kids_medical": {
        "label": "Kids-Medical",
        "description": "Pediatrician records, immunizations, dental, prescriptions for children",
    },
    "kids_activity": {
        "label": "Kids-Activity",
        "description": "Sports registrations, camp, extracurriculars, lessons, memberships",
    },
    "kids_other": {
        "label": "Kids-Other",
        "description": "Other kid-related documents that don't fit school, medical, or activities",
    },
    "government": {
        "label": "Government",
        "description": "Voter registration, jury duty, census, municipal notices, permits, licenses",
    },
    "employment": {
        "label": "Employment",
        "description": "Pay stubs, offer letters, benefits enrollment, HR correspondence",
    },
    "identity": {
        "label": "Identity",
        "description": "Passport, birth certificate, Social Security card, name change, citizenship",
    },
    "correspondence": {
        "label": "Correspondence",
        "description": "Personal letters, greeting cards, invitations, announcements",
    },
    "subscription": {
        "label": "Subscription",
        "description": "Magazine subscriptions, club memberships, loyalty programs, donations",
    },
    "junk": {
        "label": "Junk",
        "description": "Junk mail, advertisements, pre-approved offers, catalogs, coupons",
    },
    "unsorted": {
        "label": "Unsorted",
        "description": "Documents that don't clearly fit any other category",
    },
}


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class DocumentRecord:
    """Tracks every document through the pipeline."""
    bronze_source: str              # original scanned PDF filename
    bronze_sha256: str              # hash of the raw source file
    split_index: int                # which document from the split (1-based)
    split_total: int                # total documents from this scan
    page_count: int                 # pages in this document
    filename: str                   # final filename in Organized folder
    file_sha256: str                # hash of the organized copy
    category: str                   # category key
    category_label: str             # category label (appears in filename)
    sender: str                     # company or person who sent this
    document_type: str              # bill, statement, letter, etc.
    document_date: str              # date on the document (YYYY-MM-DD or "unknown")
    situation: str                  # one-line summary of what this document is about
    is_actionable: bool             # whether this requires any action
    action_note: str                # what action is needed
    processed_at: str               # when we processed it
    verified: bool                  # whether SHA-256 copy verification passed
    text_preview: str               # first ~200 chars of extracted text


# ============================================================
# FILE INTEGRITY
# ============================================================

def sha256_file(filepath: str) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


# ============================================================
# BLANK PAGE DETECTION
# ============================================================

def is_blank_page(page: fitz.Page, threshold: float = 0.98) -> bool:
    """Detect if a PDF page is blank by checking text content and pixel whiteness."""
    text = page.get_text().strip()
    if len(text) > CONFIG["blank_min_text_length"]:
        return False

    mat = fitz.Matrix(0.5, 0.5)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    total_pixels = pix.width * pix.height
    # samples_mv avoids copying pixel data; count dark pixels to determine blankness
    white_pixels = sum(1 for b in pix.samples_mv if b > 240)
    white_ratio = white_pixels / total_pixels

    logging.debug(f"    Blank analysis: text={len(text)} chars, "
                  f"white={white_ratio:.3f} (threshold={threshold})")

    return white_ratio >= threshold


def split_pdf_at_blanks(input_path: str) -> list[tuple[bytes, list[int]]]:
    """
    Split a PDF at blank separator sheets (duplex-aware).

    Since the scanner captures both sides of every sheet, pages come in pairs:
      Sheet 1 = pages 0,1  |  Sheet 2 = pages 2,3  |  Sheet 3 = pages 4,5 ...

    A SEPARATOR is a physical sheet where BOTH sides are blank (the blank
    sheet you inserted between pieces of mail).

    A single blank page (e.g. the empty back side of a one-sided letter)
    is simply omitted from the output — it does NOT trigger a split.

    Returns list of (pdf_bytes, page_indices) tuples.
    Does NOT write any files — the caller decides where to save them.
    """
    doc = fitz.open(input_path)
    total_pages = len(doc)
    logging.info(f"  Scanning {total_pages} pages for blank separators...")

    # Step 1: Identify which individual pages are blank
    blank_pages = set()
    for i in range(total_pages):
        if is_blank_page(doc[i], CONFIG["blank_threshold"]):
            blank_pages.add(i)

    # Step 2: Identify separator sheets (both sides blank)
    # Pages pair as (0,1), (2,3), (4,5), ...
    separator_sheets = set()  # store the sheet index (0, 1, 2, ...)
    for sheet in range(total_pages // 2):
        front = sheet * 2
        back = sheet * 2 + 1
        if front in blank_pages and back in blank_pages:
            separator_sheets.add(sheet)
            logging.info(f"    Sheet {sheet + 1} (pages {front + 1}-{back + 1}): "
                         f"BLANK SEPARATOR")

    # Handle odd trailing page: if the last page is alone and blank,
    # treat it as a separator (trailing blank from the last sheet)
    trailing_blank = False
    if total_pages % 2 == 1 and (total_pages - 1) in blank_pages:
        trailing_blank = True
        logging.info(f"    Page {total_pages}: trailing blank (omitted)")

    # Log individual blank backsides (not separators)
    for i in sorted(blank_pages):
        sheet = i // 2
        if sheet not in separator_sheets and not (trailing_blank and i == total_pages - 1):
            logging.info(f"    Page {i + 1}: blank backside (omitted)")

    # Step 3: Group pages into documents
    # Split at separator sheets, omit individual blank pages
    documents = []
    current_doc = []

    for i in range(total_pages):
        sheet = i // 2

        # If this page belongs to a separator sheet, it's a split point
        if sheet in separator_sheets:
            if current_doc:
                documents.append(current_doc)
                current_doc = []
            continue  # skip separator pages entirely

        # Skip trailing blank
        if trailing_blank and i == total_pages - 1:
            continue

        # Skip individual blank pages (empty backsides) but don't split
        if i in blank_pages:
            continue

        current_doc.append(i)

    if current_doc:
        documents.append(current_doc)

    if not documents:
        logging.warning("  All pages were blank!")
        doc.close()
        return []

    sep_count = len(separator_sheets)
    omitted = len(blank_pages) - (sep_count * 2) - (1 if trailing_blank else 0)
    logging.info(f"  Found {len(documents)} document(s) "
                 f"({sep_count} separator sheet(s), "
                 f"{omitted} blank backside(s) omitted)")

    # Build PDF bytes for each document
    results = []
    for page_indices in documents:
        new_doc = fitz.open()
        for page_idx in page_indices:
            new_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
        pdf_bytes = new_doc.tobytes()
        new_doc.close()
        results.append((pdf_bytes, page_indices))

    doc.close()
    return results


# ============================================================
# TEXT EXTRACTION + SEARCHABLE PDF
# ============================================================

def make_pdf_searchable(pdf_bytes: bytes) -> tuple[bytes, str]:
    """
    Make a PDF searchable by adding an invisible OCR text layer to
    image-only pages. Also extracts and returns the full text.

    Returns:
        (modified_pdf_bytes, extracted_text)
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_text = []
    modified = False

    for page in doc:
        text = page.get_text().strip()

        if text:
            # Page already has embedded text — use it
            all_text.append(text)
        elif CONFIG["ocr_if_needed"] and HAS_TESSERACT:
            # Image-only page — OCR it and embed invisible text layer
            logging.debug("    OCR on image-only page")
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # Get word-level OCR data with positions
            ocr_data = pytesseract.image_to_data(
                img, output_type=pytesseract.Output.DICT
            )

            page_text_parts = []
            scale = 72.0 / 300.0  # OCR pixels (300 DPI) → PDF points (72 DPI)
            for i, word in enumerate(ocr_data["text"]):
                if not word.strip():
                    continue

                page_text_parts.append(word)

                x = ocr_data["left"][i] * scale
                y = ocr_data["top"][i] * scale
                h = ocr_data["height"][i] * scale

                # Insert invisible text at the correct position
                try:
                    page.insert_text(
                        fitz.Point(x, y + h),  # baseline position
                        word + " ",
                        fontsize=max(h * 0.85, 1),
                        render_mode=3,  # 3 = invisible (for searchability)
                    )
                    modified = True
                except Exception as e:
                    logging.debug(f"    OCR text insert failed for '{word}': {e}")

            if page_text_parts:
                all_text.append(" ".join(page_text_parts))

    if modified:
        pdf_bytes = doc.tobytes(deflate=True, garbage=4)
        logging.info("    Added searchable text layer (OCR)")

    full_text = "\n\n".join(all_text)
    doc.close()
    return pdf_bytes, full_text


# ============================================================
# AI CLASSIFICATION AND NAMING
# ============================================================

def build_taxonomy_description() -> str:
    """Build a description of the categories for the AI prompt."""
    lines = []
    for key, info in CATEGORIES.items():
        lines.append(f'  "{key}" (label: {info["label"]}): {info["description"]}')
    return "\n".join(lines)


def ai_classify_and_name(text: str, page_count: int) -> Optional[dict]:
    """
    Use Claude to classify a document and generate metadata.

    Returns dict with keys:
        category, filename, sender, document_type, document_date,
        situation, is_actionable, action_note
    """
    if not HAS_ANTHROPIC:
        logging.debug("    AI skip: anthropic package not installed")
        return None
    if not CONFIG["use_ai_renaming"]:
        logging.debug("    AI skip: use_ai_renaming is False")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logging.debug("    AI skip: ANTHROPIC_API_KEY not set")
        return None

    text_sample = text[:4000]
    if not text_sample.strip():
        logging.debug("    AI skip: no text to classify")
        return None

    taxonomy_desc = build_taxonomy_description()

    try:
        # Reuse client across calls (module-level cache)
        if not hasattr(ai_classify_and_name, "_client"):
            ai_classify_and_name._client = anthropic.Anthropic(api_key=api_key)
        client = ai_classify_and_name._client

        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": f"""Analyze this scanned mail document and return a JSON object.

CATEGORIES (pick exactly one "category" key):
{taxonomy_desc}

RULES:
- "category": must be one of the category keys listed above
- "sender": company or person who sent this (short name, e.g. "Dominion-Energy", "Dr-Smith")
- "document_type": short doc type (e.g. "Electric-Bill", "Statement", "EOB", "Report-Card")
- "document_date": the date on the document as YYYY-MM-DD, or "unknown"
- "is_actionable": true if this requires any action (payment, response, signature, etc.)
- "situation": one-line summary of what this document is about (e.g. "Electric bill for January 2026, account ending 4821, $142.50 due"), or ""
- "action_note": brief description of action needed (e.g. "Pay $142.50 by March 15"), or ""

If this is a bill, invoice, or payment request, ALWAYS set category to "bill".
Use hyphens instead of spaces in sender and document_type (they go in filenames).

Return ONLY valid JSON, no other text.

Document ({page_count} pages):
---
{text_sample}
---"""
                }
            ]
        )

        response_text = message.content[0].text.strip()

        # Handle markdown code blocks (```json ... ``` or ``` ... ```)
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines)

        result = json.loads(response_text)

        # Validate category
        if result.get("category") not in CATEGORIES:
            logging.warning(f"    AI returned unknown category '{result.get('category')}', using 'unsorted'")
            result["category"] = "unsorted"

        # Sanitize sender and document_type for filename use
        safe_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
        for fld in ("sender", "document_type"):
            raw = result.get(fld, "Unknown")
            cleaned = "".join(c if c in safe_chars else "-" for c in raw)
            while "--" in cleaned:
                cleaned = cleaned.replace("--", "-")
            cleaned = cleaned.strip("-") or "Unknown"
            result[fld] = cleaned

        # Build the filename: YYYY-MM-DD_Category_Sender_DocType.pdf
        doc_date = result.get("document_date", "unknown")
        if doc_date == "unknown":
            doc_date = date.today().isoformat()

        category_label = CATEGORIES[result["category"]]["label"]
        filename = f"{doc_date}_{category_label}_{result['sender']}_{result['document_type']}.pdf"

        # Final sanitize
        all_safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")
        sanitized = "".join(c if c in all_safe else "-" for c in filename)
        while "--" in sanitized:
            sanitized = sanitized.replace("--", "-")
        result["filename"] = sanitized

        return result

    except json.JSONDecodeError as e:
        logging.error(f"    AI returned invalid JSON: {e}")
        return None
    except Exception as e:
        logging.error(f"    AI classification failed: {e}")
        return None


# ============================================================
# TRACKING AND LEDGER
# ============================================================

def get_unique_path(folder: str, filename: str) -> str:
    """Avoid filename collisions by appending a counter."""
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(filename)
    counter = 2
    while True:
        new_path = os.path.join(folder, f"{base}_{counter}{ext}")
        if not os.path.exists(new_path):
            return new_path
        counter += 1


def extract_scan_tag(source_filename: str) -> str:
    """
    Extract a short, unique scan tag from the source filename.

    Source filenames look like: Document_20260222_232206.pdf
    We extract the timestamp '20260222_232206', hash it, and return
    the first 6 hex chars as a compact tag.

    This tag is embedded in every output filename from this source,
    enabling quick duplicate detection and partial-run cleanup.
    """
    stem = Path(source_filename).stem
    # Try to extract YYYYMMDD_HHMMSS pattern
    parts = stem.split("_")
    # Find parts that look like a date (8 digits) and time (6 digits)
    timestamp_parts = [p for p in parts if p.isdigit() and len(p) in (6, 8)]
    if timestamp_parts:
        timestamp_key = "_".join(timestamp_parts)
    else:
        # Fallback: use the whole stem
        timestamp_key = stem

    tag = hashlib.sha256(timestamp_key.encode()).hexdigest()[:6]
    return tag


def extract_scan_datetime(source_filename: str) -> str:
    """
    Extract the scan timestamp from a scanner filename.

    Document_20260222_232206.pdf → '2026-02-22T23:22:06'
    Falls back to current time if pattern not found.
    """
    stem = Path(source_filename).stem
    parts = stem.split("_")
    date_part = None
    time_part = None
    for p in parts:
        if p.isdigit() and len(p) == 8 and date_part is None:
            date_part = p
        elif p.isdigit() and len(p) == 6 and time_part is None:
            time_part = p
    if date_part and time_part:
        try:
            dt = datetime.strptime(f"{date_part}_{time_part}", "%Y%m%d_%H%M%S")
            return dt.isoformat()
        except ValueError:
            pass
    return datetime.now().isoformat()


def find_files_by_scan_tag(folder: str, scan_tag: str) -> list[str]:
    """Find all PDFs in a folder whose filename contains the scan tag."""
    if not os.path.isdir(folder):
        return []
    return [
        str(f) for f in Path(folder).glob("*.pdf")
        if f"_{scan_tag}" in f.stem
    ]


def cleanup_partial_run(folder: str, scan_tag: str) -> int:
    """Delete output files from a previous partial run of the same source.
    Returns the number of files removed."""
    existing = find_files_by_scan_tag(folder, scan_tag)
    for f in existing:
        os.remove(f)
        logging.info(f"    Removed partial: {os.path.basename(f)}")
    return len(existing)


def load_processing_ledger(tracking_folder: str) -> dict:
    """Load the ledger of processed raw files (keyed by SHA-256)."""
    ledger_path = os.path.join(tracking_folder, "processing_ledger.json")
    if os.path.exists(ledger_path):
        with open(ledger_path, "r") as f:
            return json.load(f)
    return {}


def save_processing_ledger(tracking_folder: str, ledger: dict):
    """Save the processing ledger atomically."""
    ledger_path = os.path.join(tracking_folder, "processing_ledger.json")
    tmp_path = ledger_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(ledger, f, indent=2)
        os.replace(tmp_path, ledger_path)  # atomic on POSIX
    except OSError as e:
        logging.error(f"Failed to save ledger: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def append_document_record(tracking_folder: str, record: DocumentRecord):
    """Append a document record to the CSV index."""
    csv_path = os.path.join(tracking_folder, "document_index.csv")
    file_exists = os.path.exists(csv_path)
    fieldnames = list(asdict(record).keys())

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(asdict(record))


def append_todo(record: DocumentRecord):
    """Append an action item to TODO.md when a document needs attention.

    Uses a markdown table sorted newest-first with relative links
    to the document in the Organized/ folder.
    """
    if not record.is_actionable:
        return

    # TODO.md lives in the Reports folder
    todo_path = os.path.join(CONFIG["tracking_folder"], "TODO.md")

    # Relative link from Reports/TODO.md → Organized/filename.pdf
    link = f"[📄 View](../Organized/{record.filename})"

    date_str = record.document_date if record.document_date != "unknown" else ""
    # Situation is last column — hidden in HTML, shown in expandable detail
    new_row = (f"| ☐ | {date_str} | {record.sender} "
               f"| {record.action_note} | {link} "
               f"| {record.situation} |")

    header = (
        "# 📬 Mail Action Items\n"
        "\n"
        "| Status | Date | From | Action | Document | Situation |\n"
        "|--------|------|------|--------|----------|-----------|\n"
    )

    # Read existing rows (skip header lines)
    existing_rows = []
    if os.path.exists(todo_path):
        with open(todo_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip()
                if line.startswith("| "):
                    # Skip header and separator rows
                    if line.startswith("| Status") or line.startswith("|---"):
                        continue
                    existing_rows.append(line)

    # Dedupe: don't add if this filename is already in the table
    if any(record.filename in row for row in existing_rows):
        return

    # Insert new row at the top (newest first)
    existing_rows.insert(0, new_row)

    # Rewrite the file
    with open(todo_path, "w", encoding="utf-8") as f:
        f.write(header)
        for row in existing_rows:
            f.write(row + "\n")

    logging.info(f"    📝 Added to TODO.md")




# ============================================================
# MAIN PROCESSING PIPELINE
# ============================================================

def process_bronze_file(bronze_path: str) -> list[DocumentRecord]:
    """
    Full pipeline for one raw scanned PDF:
    1. Hash the raw file (for dedup and verification)
    2. Split at blank pages (in memory — Raw folder is read-only)
    3. For each split document:
       a. Make searchable (OCR)
       b. AI classify + name
       c. Write to Organized layer
       d. Verify the copy
       e. Record in ledger and trackers
    4. Update processing ledger
    """
    tracking_folder = CONFIG["tracking_folder"]
    silver_root = CONFIG["silver_folder"]

    # ---- Step 1: Validate and dedup ----
    file_size = os.path.getsize(bronze_path)
    if file_size == 0:
        logging.warning(f"  Skipping empty file: {os.path.basename(bronze_path)}")
        return []

    bronze_hash = sha256_file(bronze_path)
    scan_tag = extract_scan_tag(os.path.basename(bronze_path))
    ledger = load_processing_ledger(tracking_folder)

    if bronze_hash in ledger:
        logging.info(f"  Already processed (SHA-256 match) — skipping")
        return []

    # Check for leftover files from a partial previous run
    leftover = find_files_by_scan_tag(silver_root, scan_tag)
    if leftover:
        logging.info(f"  Found {len(leftover)} file(s) from partial previous run — cleaning up")
        cleanup_partial_run(silver_root, scan_tag)

    logging.info(f"\n{'=' * 65}")
    logging.info(f"PROCESSING: {os.path.basename(bronze_path)}")
    logging.info(f"  Bronze SHA-256: {bronze_hash[:16]}...")
    logging.info(f"  Scan tag: {scan_tag}")
    logging.info(f"  Scan time: {extract_scan_datetime(os.path.basename(bronze_path))}")
    logging.info(f"{'=' * 65}")

    # ---- Step 2: Split at blank pages (read-only — all in memory) ----
    split_results = split_pdf_at_blanks(bronze_path)

    if not split_results:
        logging.warning("  No documents extracted — skipping")
        return []

    # ---- Step 3: Process each split document ----
    records = []
    scan_time = extract_scan_datetime(os.path.basename(bronze_path))
    os.makedirs(silver_root, exist_ok=True)

    for idx, (pdf_bytes, page_indices) in enumerate(split_results):
        doc_num = idx + 1
        total_docs = len(split_results)
        logging.info(f"\n  --- Document {doc_num} of {total_docs} "
                     f"({len(page_indices)} pages) ---")

        # 3a: Make searchable (embed OCR text layer) + extract text
        pdf_bytes, text = make_pdf_searchable(pdf_bytes)
        text_preview = text[:200].replace("\n", " ").strip() if text else "(no text)"
        logging.info(f"    Text: {text_preview[:80]}...")

        # 3b: AI classify and name
        ai_result = ai_classify_and_name(text, len(page_indices))

        if ai_result:
            category = ai_result["category"]
            # Embed scan tag into AI filename for dedup/cleanup
            base, ext = os.path.splitext(ai_result["filename"])
            filename = f"{base}_{scan_tag}{ext}"
            sender = ai_result.get("sender", "Unknown")
            doc_type = ai_result.get("document_type", "Document")
            doc_date = ai_result.get("document_date", "unknown")
            situation = ai_result.get("situation", "")
            is_actionable = ai_result.get("is_actionable", False)
            action_note = ai_result.get("action_note", "")

            logging.info(f"    AI: {filename}")
            if is_actionable:
                logging.info(f"    ⚡ Action needed: {action_note}")
        else:
            # Fallback: date + Unsorted + scan tag + counter
            category = "unsorted"
            timestamp = scan_time[:10]  # YYYY-MM-DD from scan datetime
            filename = f"{timestamp}_Unsorted_{scan_tag}_doc-{doc_num:03d}.pdf"
            sender = "Unknown"
            doc_type = "Document"
            doc_date = "unknown"
            situation = ""
            is_actionable = False
            action_note = ""
            logging.info(f"    Fallback: {filename}")

        category_label = CATEGORIES[category]["label"]

        # 3c: Write to Organized folder (flat — no subfolders)
        out_path = get_unique_path(silver_root, filename)
        with open(out_path, "wb") as f:
            f.write(pdf_bytes)

        final_filename = os.path.basename(out_path)
        logging.info(f"    Saved: {final_filename}")

        # 3d: Verify the copy
        file_hash = sha256_file(out_path)
        source_hash = sha256_bytes(pdf_bytes)
        verified = file_hash == source_hash

        if verified:
            logging.info(f"    ✓ Copy verified (SHA-256 match)")
        else:
            logging.error(f"    ✗ COPY VERIFICATION FAILED!")

        # 3e: Build record
        record = DocumentRecord(
            bronze_source=os.path.basename(bronze_path),
            bronze_sha256=bronze_hash,
            split_index=doc_num,
            split_total=total_docs,
            page_count=len(page_indices),
            filename=final_filename,
            file_sha256=file_hash,
            category=category,
            category_label=category_label,
            sender=sender,
            document_type=doc_type,
            document_date=doc_date,
            situation=situation,
            is_actionable=is_actionable,
            action_note=action_note,
            processed_at=scan_time,
            verified=verified,
            text_preview=text_preview[:200],
        )
        records.append(record)
        append_document_record(tracking_folder, record)
        append_todo(record)

    # ---- Step 4: Update ledger ----
    ledger[bronze_hash] = {
        "filename": os.path.basename(bronze_path),
        "processed_at": datetime.now().isoformat(),
        "documents": len(records),
        "all_verified": all(r.verified for r in records),
    }
    save_processing_ledger(tracking_folder, ledger)

    all_ok = all(r.verified for r in records)
    if all_ok:
        logging.info(f"\n  ✓ ALL {len(records)} DOCUMENTS VERIFIED")
    else:
        logging.error(f"\n  ✗ SOME COPIES FAILED VERIFICATION")

    return records


# ============================================================
# FOLDER WATCHER
# ============================================================

def watch_folder():
    """Watch the Raw folder for incoming scans using simple directory polling.

    Uses the processing ledger to know what's already been handled.
    Waits one extra cycle after detecting a new file to ensure it's
    finished writing before processing.
    """
    bronze = CONFIG["bronze_folder"]
    poll_interval = CONFIG.get("poll_interval", 15)

    logging.info(f"Raw (scanner inbox): {bronze}")
    logging.info(f"Organized:          {CONFIG['silver_folder']}")
    logging.info(f"Reports:            {CONFIG['tracking_folder']}")
    logging.info(f"AI renaming:  {'ON' if CONFIG['use_ai_renaming'] and HAS_ANTHROPIC else 'OFF'}")
    logging.info(f"OCR fallback: {'ON' if HAS_TESSERACT else 'OFF'}")
    logging.info(f"\nPolling every {poll_interval}s... (Ctrl+C to stop)\n")

    # Seed with existing files so we don't re-alert on them
    pending: dict[str, int] = {}
    already_seen: set[str] = set()
    for f in Path(bronze).glob("*.pdf"):
        try:
            already_seen.add(str(f))
        except OSError:
            pass

    try:
        while True:
            time.sleep(poll_interval)

            for f in sorted(Path(bronze).glob("*.pdf")):
                path = str(f)
                try:
                    size = f.stat().st_size
                except OSError:
                    continue

                if size == 0:
                    continue

                # Skip files that existed at startup (already handled by process_existing_files)
                if path in already_seen:
                    continue

                if path in pending:
                    if pending[path] == size:
                        # Size stable — file is done writing, process it
                        del pending[path]
                        already_seen.add(path)
                        try:
                            process_bronze_file(path)
                        except Exception as e:
                            logging.error(f"Error processing {path}: {e}",
                                          exc_info=True)
                    else:
                        # Still changing — update and wait another cycle
                        pending[path] = size
                else:
                    # New file — record size and wait one cycle for write to finish
                    pending[path] = size
                    logging.info(f"New scan detected: {os.path.basename(path)} "
                                 f"({size:,} bytes) — waiting for write to finish")

    except KeyboardInterrupt:
        logging.info("\nStopping...")


def process_existing_files():
    """Batch-process all PDFs currently in the Raw folder."""
    bronze = CONFIG["bronze_folder"]
    pdf_files = sorted(Path(bronze).glob("*.pdf"))

    if not pdf_files:
        logging.info(f"No PDF files in {bronze}")
        return

    logging.info(f"Found {len(pdf_files)} PDF(s) to process")

    for pdf_path in pdf_files:
        try:
            process_bronze_file(str(pdf_path))
        except Exception as e:
            logging.error(f"Error processing {pdf_path}: {e}", exc_info=True)


# ============================================================
# LOCAL HTTP SERVER (for TODO.html dashboard)
# ============================================================

def start_http_server(directory: str, port: int):
    """Start a background HTTP server serving the HomeMail folder.

    Runs as a daemon thread so it dies when the main process exits.
    Serves TODO.html, TODO.md, and Organized/ folder for doc links.
    """
    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)
        def log_message(self, format, *args):
            pass  # suppress per-request logging

    class ReusableTCPServer(TCPServer):
        allow_reuse_address = True

    for attempt in range(5):
        try:
            server = ReusableTCPServer(("0.0.0.0", port), QuietHandler)
            break
        except OSError as e:
            if attempt < 4:
                logging.debug(f"Port {port} busy, retrying in 3s... ({attempt + 1}/5)")
                time.sleep(3)
            else:
                logging.warning(f"Could not start HTTP server on port {port}: {e}")
                return

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logging.info(f"📊 Dashboard: http://localhost:{port}/Reports/")


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Mail Scanner Pipeline — Raw / Organized Architecture"
    )
    parser.add_argument("--bronze", metavar="FOLDER", help="Raw layer folder")
    parser.add_argument("--silver", metavar="FOLDER", help="Organized layer folder")
    parser.add_argument("--batch", action="store_true", help="Process existing files and exit")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI classification")
    parser.add_argument("--threshold", type=float, default=0.98,
                        help="Blank page threshold (0-1, default 0.98)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument("--port", type=int, default=8080,
                        help="HTTP server port for TODO dashboard (default 8080, 0 to disable)")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.bronze:
        CONFIG["bronze_folder"] = args.bronze
    if args.silver:
        CONFIG["silver_folder"] = args.silver
    if args.no_ai:
        CONFIG["use_ai_renaming"] = False
    CONFIG["blank_threshold"] = args.threshold

    # Create folder structure
    os.makedirs(CONFIG["bronze_folder"], exist_ok=True)
    os.makedirs(CONFIG["silver_folder"], exist_ok=True)
    os.makedirs(CONFIG["tracking_folder"], exist_ok=True)

    logging.info("Mail Scanner Pipeline v2")
    logging.info("=" * 40)

    if not HAS_ANTHROPIC:
        logging.warning("anthropic package not installed — AI disabled")
        logging.warning("  Run with: uv run pipeline.py (deps install automatically)")
        CONFIG["use_ai_renaming"] = False
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        logging.warning("ANTHROPIC_API_KEY not set — AI disabled (all files will be named 'Unsorted')")
        logging.warning("  Set it with: export ANTHROPIC_API_KEY='sk-ant-...'")
        CONFIG["use_ai_renaming"] = False
    else:
        key = os.environ["ANTHROPIC_API_KEY"]
        logging.info(f"AI renaming: ON (key: ...{key[-4:]})")

    if not HAS_TESSERACT:
        logging.warning("Tesseract OCR not available — install with: sudo apt install tesseract-ocr")
        logging.info("  (OCR fallback disabled — pages without embedded text will be skipped)")

    # Start dashboard server (serves HomeMail root: TODO.html, TODO.md, Organized/)
    if args.port:
        homemail_root = os.path.dirname(CONFIG["silver_folder"])
        start_http_server(homemail_root, args.port)

    if args.batch:
        process_existing_files()
    else:
        # Process any files already waiting, then watch for new ones
        process_existing_files()
        watch_folder()


if __name__ == "__main__":
    main()
