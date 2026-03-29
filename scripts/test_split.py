#!/usr/bin/python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pymupdf",
#     "anthropic",
# ]
# ///
"""Quick test: run ai_split_pdf and split_pdf_at_blanks on test files and compare."""

import os
import sys
import json
import base64
import logging

import fitz

# Ensure ANTHROPIC_API_KEY is set
if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not set")
    sys.exit(1)

import anthropic

logging.basicConfig(level=logging.DEBUG, format="%(message)s")

# ---- Inline the two split functions so we don't need to import the full pipeline ----

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "_pipeline"))
from pipeline import split_pdf_at_blanks, ai_split_pdf, CONFIG

TEST_FILES = [
    "Raw/CVS-Various-Pharmacy-March-2026.pdf",
    "Raw/Marsha-Medical-March-2026.pdf",
]

for filepath in TEST_FILES:
    if not os.path.exists(filepath):
        print(f"SKIP: {filepath} not found")
        continue

    doc = fitz.open(filepath)
    total_pages = len(doc)
    doc.close()

    print(f"\n{'=' * 65}")
    print(f"FILE: {filepath} ({total_pages} pages)")
    print(f"{'=' * 65}")

    # --- Blank-page method ---
    print(f"\n--- split_pdf_at_blanks ---")
    blank_results = split_pdf_at_blanks(filepath)
    print(f"  Documents found: {len(blank_results)}")
    for i, (pdf_bytes, pages) in enumerate(blank_results):
        page_list = [p + 1 for p in pages]  # 1-based for display
        print(f"  Doc {i+1}: pages {page_list} ({len(pages)} pages, {len(pdf_bytes)} bytes)")

    # --- AI method ---
    print(f"\n--- ai_split_pdf ---")
    ai_results = ai_split_pdf(filepath)
    if ai_results is None:
        print("  AI split returned None (failed)")
    else:
        print(f"  Documents found: {len(ai_results)}")
        for i, (pdf_bytes, pages) in enumerate(ai_results):
            page_list = [p + 1 for p in pages]  # 1-based for display
            print(f"  Doc {i+1}: pages {page_list} ({len(pages)} pages, {len(pdf_bytes)} bytes)")

    # --- Compare ---
    print(f"\n--- Comparison ---")
    if ai_results is None:
        print("  Cannot compare: AI split failed")
    elif len(blank_results) == len(ai_results):
        match = all(b[1] == a[1] for b, a in zip(blank_results, ai_results))
        if match:
            print("  MATCH: Both methods produced identical splits")
        else:
            print("  DIFFER: Same document count but different page groupings")
    else:
        print(f"  DIFFER: blank={len(blank_results)} docs, ai={len(ai_results) if ai_results else 0} docs")