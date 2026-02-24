"""
reprocess_sinhala.py
=====================
Re-summarises all Sinhala circulars in the DB that currently have
English summaries. Extracts text directly from PDFs if txt file missing.
"""

import os
import re
import sqlite3
import time
from pathlib import Path

import fitz  # PyMuPDF
from groq import Groq

# ── Config ────────────────────────────────────────────────────────────────────
GROQ_API_KEY      = os.environ.get('GROQ_API_KEY', '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')
DB_FILE           = 'circulars.db'
TEXT_DIR          = Path('extracted_text')
DOWNLOAD_DIR      = Path('downloads')
MODEL_NAME        = 'llama-3.1-8b-instant'
MAX_TEXT_CHARS_SI = 600
SINHALA_RE        = re.compile(r'[\u0d80-\u0dff]')
DELAY_SECONDS     = 12


def get_english_sinhala_rows():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("""
        SELECT circular_number, issued_date, topic, summary, txt_path, pdf_path
        FROM   circulars WHERE language = 'S'
    """).fetchall()
    conn.close()
    needs_fix = []
    for number, date, topic, summary, txt_path, pdf_path in rows:
        combined = (topic or '') + (summary or '')
        if len(SINHALA_RE.findall(combined)) <= 5:
            needs_fix.append({
                'number'  : number,
                'date'    : date or '',
                'topic'   : topic or '',
                'txt_path': txt_path or '',
                'pdf_path': pdf_path or '',
            })
    return needs_fix


def safe_stem(number: str) -> str:
    """Convert circular number to safe filename stem. 06/2025 (Letter) → 06-2025 (Letter)"""
    return ''.join(
        c for c in number.replace('/', '-').replace('\\', '-')
        if c not in ':*?"<>|'
    ).strip()


def find_pdf(row: dict) -> Path | None:
    """Find the Sinhala PDF for this circular."""

    # 1. Try stored pdf_path from DB
    if row['pdf_path']:
        p = Path(row['pdf_path'].replace('\\', '/'))
        if p.exists():
            return p

    # 2. Search downloads/**/Sinhala/ with stem variants
    stem     = safe_stem(row['number'])
    # Extract year from number e.g. '06/2025' → '2025', '02/2026' → '2026'
    year_match = re.search(r'(20\d\d)', row['number'])
    year       = year_match.group(1) if year_match else None

    variants = [
        stem + '.pdf',
        re.sub(r'\s*\(.*?\)', '', stem).strip() + '.pdf',   # strip suffix
        stem.replace(' ', '_') + '.pdf',
    ]

    search_dirs = []
    if year:
        si_dir = DOWNLOAD_DIR / year / 'Sinhala'
        if si_dir.exists():
            search_dirs.append(si_dir)
    # also search all years
    for d in DOWNLOAD_DIR.rglob('Sinhala'):
        if d not in search_dirs:
            search_dirs.append(d)

    for d in search_dirs:
        for v in variants:
            p = d / v
            if p.exists():
                return p

    # 3. Fuzzy — find any PDF in Sinhala dirs starting with number prefix
    prefix = row['number'].split('/')[0].strip()
    for pdf in DOWNLOAD_DIR.rglob('Sinhala/*.pdf'):
        if pdf.stem.startswith(prefix + '-'):
            if not year or year in pdf.stem:
                return pdf

    return None


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from PDF using PyMuPDF."""
    doc  = fitz.open(pdf_path)
    text = ''
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def build_prompt(row, text):
    return f"""You are analysing a Sri Lanka government circular written in Sinhala.
You MUST reply in Sinhala language only. Do not use English at all.

Extract these fields and reply in EXACT format, no extra text:

TOPIC: (short title in Sinhala)
SUMMARY: (2-3 sentences in Sinhala)
ISSUED_BY: (ministry or authority in Sinhala)
ISSUED_DATE: (YYYY-MM-DD or null)
APPLIES_TO: (who it applies to, in Sinhala)
DEADLINE: (deadline date or null)

Circular: {row['number']} | Date: {row['date']}

Text:
{text[:MAX_TEXT_CHARS_SI]}"""


def parse_response(raw):
    result  = {}
    mapping = {
        'TOPIC': 'topic', 'SUMMARY': 'summary', 'ISSUED_BY': 'issued_by',
        'ISSUED_DATE': 'issued_date', 'APPLIES_TO': 'applies_to', 'DEADLINE': 'deadline',
    }
    for line in raw.strip().splitlines():
        if ':' in line:
            key, _, val = line.partition(':')
            key = key.strip().upper()
            val = val.strip()
            if val.lower() == 'null':
                val = None
            if key in mapping:
                result[mapping[key]] = val
    return result


def update_db(number, summary):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        UPDATE circulars
        SET topic=?, summary=?, issued_by=?, applies_to=?, deadline=?
        WHERE circular_number=? AND language='S'
    """, (
        summary.get('topic'), summary.get('summary'),
        summary.get('issued_by'), summary.get('applies_to'),
        summary.get('deadline'), number,
    ))
    conn.commit()
    conn.close()


def main():
    client = Groq(api_key=GROQ_API_KEY)

    print('\n' + '='*60)
    print('  Sinhala Re-processor  (reads directly from PDFs)')
    print('='*60)

    rows = get_english_sinhala_rows()
    print(f'\nFound {len(rows)} Sinhala rows with English summaries\n')

    if not rows:
        print('✅ Nothing to fix!')
        return

    fixed = skipped = failed = 0

    for i, row in enumerate(rows, 1):
        print(f'[{i}/{len(rows)}] {row["number"]}')

        # Find PDF directly
        pdf_path = find_pdf(row)
        if not pdf_path:
            print(f'  ❌ No PDF found on disk — skipping')
            skipped += 1
            continue

        print(f'  📄 PDF: {pdf_path}')

        # Extract text from PDF
        try:
            text = extract_text_from_pdf(pdf_path)
        except Exception as e:
            print(f'  ❌ Extraction failed: {e}')
            failed += 1
            continue

        si_in_text = len(SINHALA_RE.findall(text))
        print(f'  {len(text)} chars, {si_in_text} Sinhala chars')

        if si_in_text == 0:
            print(f'  ⚠️  No Sinhala chars in PDF — skipping')
            skipped += 1
            continue

        # Send to Groq
        summary = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{'role': 'user', 'content': build_prompt(row, text)}],
                    max_tokens=400,
                    timeout=30,
                )
                summary = parse_response(resp.choices[0].message.content)
                break
            except Exception as ex:
                err = str(ex)
                if '429' in err or 'rate_limit' in err:
                    wait = 20 * (attempt + 1)
                    print(f'  ⏳ Rate limit — waiting {wait}s')
                    time.sleep(wait)
                else:
                    print(f'  ❌ Groq error: {ex}')
                    break

        if not summary:
            failed += 1
            continue

        topic  = summary.get('topic', '')
        si_new = len(SINHALA_RE.findall((topic or '') + (summary.get('summary') or '')))
        update_db(row['number'], summary)

        if si_new > 5:
            print(f'  ✅ Fixed!  {topic[:55]}')
            fixed += 1
        else:
            print(f'  ⚠️  Still English: "{topic[:55]}"')
            failed += 1

        print(f'  Waiting {DELAY_SECONDS}s...')
        time.sleep(DELAY_SECONDS)

    print(f'\n{"="*60}')
    print(f'  Fixed   : {fixed}')
    print(f'  Skipped : {skipped}  (no PDF on disk)')
    print(f'  Failed  : {failed}  (Groq still English)')
    print(f'{"="*60}\n')


if __name__ == '__main__':
    main()
