#!/usr/bin/python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf"]
# ///
"""Render each page of a PDF as a PNG for visual inspection."""
import sys, os, fitz

for name in ["Raw/CVS-Various-Pharmacy-March-2026.pdf", "Raw/Marsha-Medical-March-2026.pdf"]:
    doc = fitz.open(name)
    base = os.path.splitext(os.path.basename(name))[0]
    os.makedirs(f"/tmp/homemail_preview/{base}", exist_ok=True)
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(dpi=150)
        out = f"/tmp/homemail_preview/{base}/page_{i+1}.png"
        pix.save(out)
        print(f"  {out}")
    doc.close()