"""
check_sinhala.py — Sinhala PDF Diagnostic Tool
================================================
Checks every Sinhala PDF in your downloads folder and tells you:
  ✅ GOOD     — native text extracted correctly (Sinhala Unicode found)
  ⚠️  LATIN   — text extracted but only Latin/ASCII (font encoding issue)
  🔍 SCANNED  — almost empty text (needs OCR)
  ❌ EMPTY    — completely blank (bad PDF or download failed)

Also checks your circulars.db to compare what was stored vs what's in the PDF.

Run:
    python check_sinhala.py

Output:
    - Prints full report to console
    - Saves  sinhala_check_report.json  for reference
"""

import json
import os
import re
import sqlite3
from pathlib import Path

import fitz   # PyMuPDF — pip install pymupdf

# ── Config ────────────────────────────────────────────────────────────────────
DOWNLOAD_DIR = Path('downloads')
DB_FILE      = 'circulars.db'
REPORT_FILE  = 'sinhala_check_report.json'

# Sinhala Unicode block: U+0D80 to U+0DFF
# If a text contains characters in this range → real Sinhala text extracted
SINHALA_RE   = re.compile(r'[\u0d80-\u0dff]')

# Thresholds
MIN_CHARS_GOOD    = 100   # at least 100 chars = meaningful text
MIN_SINHALA_RATIO = 0.05  # at least 5% of chars should be Sinhala Unicode


# ── Helpers ───────────────────────────────────────────────────────────────────

def classify_text(text: str) -> tuple[str, str]:
    """
    Classify extracted text into one of four categories.
    Returns (status_code, description)
    """
    total_chars   = len(text.strip())
    sinhala_chars = len(SINHALA_RE.findall(text))

    if total_chars < 20:
        return 'EMPTY', f'Only {total_chars} chars — blank PDF or download failed'

    if total_chars < MIN_CHARS_GOOD:
        return 'SCANNED', f'{total_chars} chars — too little text, likely scanned image'

    sinhala_ratio = sinhala_chars / total_chars if total_chars > 0 else 0

    if sinhala_chars == 0:
        return 'LATIN', (
            f'{total_chars} chars extracted but ZERO Sinhala Unicode — '
            f'font encoding issue (text is Latin/ASCII only)'
        )

    if sinhala_ratio < MIN_SINHALA_RATIO:
        return 'LATIN', (
            f'{sinhala_chars}/{total_chars} Sinhala chars ({sinhala_ratio:.1%}) — '
            f'mostly Latin, likely English content or encoding problem'
        )

    return 'GOOD', (
        f'{total_chars} chars, {sinhala_chars} Sinhala Unicode chars '
        f'({sinhala_ratio:.1%}) — correctly extracted ✅'
    )


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract all text from a PDF using PyMuPDF (native, no OCR)."""
    try:
        doc  = fitz.open(pdf_path)
        text = ''
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        return f'ERROR: {e}'


def load_db_sinhala() -> dict:
    """Load stored Sinhala summaries from circulars.db."""
    if not Path(DB_FILE).exists():
        return {}
    conn  = sqlite3.connect(DB_FILE)
    rows  = conn.execute(
        "SELECT circular_number, topic, summary FROM circulars WHERE language = 'S'"
    ).fetchall()
    conn.close()
    return {r[0]: {'topic': r[1], 'summary': r[2]} for r in rows}


def show_sinhala_sample(text: str, n: int = 3) -> list[str]:
    """Extract up to n lines that contain Sinhala characters."""
    lines   = [l.strip() for l in text.splitlines() if SINHALA_RE.search(l) and len(l.strip()) > 5]
    return lines[:n]


# ── Main check ────────────────────────────────────────────────────────────────

def check_all_sinhala_pdfs() -> dict:
    """
    Find all PDFs in downloads/*/Sinhala/ and check each one.
    Returns structured results dict.
    """
    # Find all Sinhala PDFs
    sinhala_pdfs = sorted(DOWNLOAD_DIR.rglob('Sinhala/*.pdf'))

    if not sinhala_pdfs:
        print(f'\n❌  No Sinhala PDFs found in {DOWNLOAD_DIR}/')
        print('   Expected structure: downloads/2025/Sinhala/*.pdf')
        return {}

    db_data = load_db_sinhala()

    results = {
        'GOOD'   : [],
        'LATIN'  : [],
        'SCANNED': [],
        'EMPTY'  : [],
    }

    print(f'\n{"═"*65}')
    print(f'  SINHALA PDF DIAGNOSTIC  —  {len(sinhala_pdfs)} PDFs found')
    print(f'{"═"*65}\n')

    for pdf_path in sinhala_pdfs:
        # Derive circular number from filename
        # e.g.  downloads/2025/Sinhala/10-2025.pdf  →  10/2025
        stem   = pdf_path.stem                      # e.g. "10-2025"
        parts  = stem.split('-')
        circ_num = f'{parts[0]}/{parts[1]}' if len(parts) == 2 else stem

        # Extract text
        text   = extract_text_from_pdf(pdf_path)
        status, description = classify_text(text)

        # Check DB
        db_entry    = db_data.get(circ_num, {})
        db_topic    = db_entry.get('topic', '')
        db_summary  = db_entry.get('summary', '')
        in_db       = bool(db_topic or db_summary)

        # DB Sinhala quality check
        db_status = '—'
        if in_db:
            db_sinhala = len(SINHALA_RE.findall(db_topic + db_summary))
            if db_sinhala > 0:
                db_status = f'✅ {db_sinhala} Sinhala chars in DB'
            else:
                db_status = '⚠️  DB has Latin text only (AI gave English response)'

        # Sample lines
        sample = show_sinhala_sample(text)

        record = {
            'circular_number': circ_num,
            'pdf_path'       : str(pdf_path),
            'status'         : status,
            'description'    : description,
            'in_db'          : in_db,
            'db_status'      : db_status,
            'db_topic'       : db_topic,
            'sample_lines'   : sample,
            'total_chars'    : len(text.strip()),
        }
        results[status].append(record)

        # Print per-file result
        icon = {'GOOD': '✅', 'LATIN': '⚠️ ', 'SCANNED': '🔍', 'EMPTY': '❌'}[status]
        print(f'{icon} [{status:7s}]  {circ_num:20s}  {pdf_path.name}')
        print(f'           {description}')

        if status == 'GOOD' and sample:
            print(f'           Sample: "{sample[0][:60]}"')
        elif status == 'LATIN':
            # Show a sample of what was extracted to understand the issue
            first_line = next((l.strip() for l in text.splitlines() if len(l.strip()) > 10), '')
            print(f'           Extracted: "{first_line[:70]}"')
        elif status == 'SCANNED':
            print(f'           → Needs OCR  (run_pipeline.py will handle this automatically)')

        if in_db:
            print(f'           DB: {db_status}')
            if db_topic:
                print(f'           DB topic: "{db_topic[:65]}"')
        else:
            print(f'           DB: not yet stored')

        print()

    return results


def print_summary(results: dict):
    total   = sum(len(v) for v in results.values())
    n_good  = len(results['GOOD'])
    n_latin = len(results['LATIN'])
    n_scan  = len(results['SCANNED'])
    n_empty = len(results['EMPTY'])

    print(f'{"═"*65}')
    print(f'  SUMMARY')
    print(f'{"═"*65}')
    print(f'  Total Sinhala PDFs  : {total}')
    print(f'  ✅ GOOD  (correct)  : {n_good}')
    print(f'  ⚠️  LATIN (encoding) : {n_latin}')
    print(f'  🔍 SCANNED (needs OCR): {n_scan}')
    print(f'  ❌ EMPTY (bad PDF)  : {n_empty}')
    print(f'{"═"*65}\n')

    # Actionable advice
    if n_good == total:
        print('🎉 All Sinhala PDFs extracted correctly! No action needed.\n')
        return

    if n_latin > 0:
        print(f'⚠️  {n_latin} PDFs have LATIN encoding issues:')
        print('   These PDFs use non-standard fonts. The PDF renders as Sinhala')
        print('   visually, but the underlying bytes are Latin characters.')
        print('   FIX: These need OCR even though text was "extracted".')
        print('   → In run_pipeline.py, lower MIN_CHARS_PAGE and rely on OCR.\n')
        for r in results['LATIN']:
            print(f'   • {r["circular_number"]}  ({r["pdf_path"]})')
        print()

    if n_scan > 0:
        print(f'🔍 {n_scan} PDFs are SCANNED images (need OCR):')
        print('   The new run_pipeline.py handles these automatically.')
        print('   Make sure tesseract-ocr-sin is installed:\n')
        print('   Ubuntu:  sudo apt-get install tesseract-ocr tesseract-ocr-sin')
        print('   Windows: install Tesseract + tick Sinhala during setup\n')
        for r in results['SCANNED']:
            print(f'   • {r["circular_number"]}  ({r["pdf_path"]})')
        print()

    if n_empty > 0:
        print(f'❌ {n_empty} PDFs are EMPTY — possibly corrupt or download failed:')
        for r in results['EMPTY']:
            print(f'   • {r["circular_number"]}  ({r["pdf_path"]})')
        print('   → Delete these files and re-run the pipeline to re-download.\n')


def save_report(results: dict):
    # Convert sets to lists for JSON serialisation
    flat = []
    for status, records in results.items():
        flat.extend(records)
    flat.sort(key=lambda x: x['circular_number'])

    report = {
        'summary': {s: len(v) for s, v in results.items()},
        'details': flat,
    }
    Path(REPORT_FILE).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    print(f'📄 Full report saved → {REPORT_FILE}\n')


# ── Quick OCR test ────────────────────────────────────────────────────────────

def test_ocr_on_one(pdf_path: Path):
    """
    Quick test: run OCR on the first page of one PDF and show the result.
    Useful to verify Tesseract + Sinhala language pack is working.
    """
    print(f'\n── OCR TEST on: {pdf_path.name} ──')
    try:
        import pytesseract
        from PIL import Image
        import io

        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            print('  ❌ PDF has no pages')
            return
        page = doc[0]
        mat  = fitz.Matrix(200 / 72, 200 / 72)
        pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img  = Image.open(io.BytesIO(pix.tobytes('png')))
        doc.close()

        text = pytesseract.image_to_string(img, lang='sin')
        sinhala_chars = len(SINHALA_RE.findall(text))

        print(f'  OCR extracted {len(text)} chars, {sinhala_chars} Sinhala Unicode chars')
        if sinhala_chars > 0:
            print('  ✅ Tesseract Sinhala OCR is working!')
            lines = show_sinhala_sample(text, 5)
            for l in lines:
                print(f'  → {l[:70]}')
        else:
            print('  ⚠️  OCR ran but got no Sinhala chars.')
            print('  Check: is tesseract-ocr-sin installed?')
            print('  Ubuntu:  sudo apt-get install tesseract-ocr-sin')
            print('  Check:   tesseract --list-langs  (should include "sin")')
            first = next((l for l in text.splitlines() if l.strip()), '')
            print(f'  What OCR got: "{first[:80]}"')

    except ImportError:
        print('  ❌ pytesseract or Pillow not installed')
        print('     pip install pytesseract Pillow')
    except Exception as e:
        print(f'  ❌ OCR test failed: {e}')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    results = check_all_sinhala_pdfs()
    if not results:
        return

    print_summary(results)
    save_report(results)

    # If any PDFs are SCANNED, offer OCR test on the first one
    scanned = results.get('SCANNED', [])
    if scanned:
        first_scanned = Path(scanned[0]['pdf_path'])
        print(f'Running OCR test on first scanned PDF: {first_scanned.name}')
        test_ocr_on_one(first_scanned)

    # Also test OCR on a LATIN PDF if any exist
    latin = results.get('LATIN', [])
    if latin and not scanned:
        first_latin = Path(latin[0]['pdf_path'])
        print(f'Running OCR test on first LATIN-encoded PDF: {first_latin.name}')
        test_ocr_on_one(first_latin)


if __name__ == '__main__':
    main()
