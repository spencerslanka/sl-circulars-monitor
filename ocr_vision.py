"""
ocr_vision.py — Google Vision OCR with Monthly Free Tier Cap
=============================================================
- Hard cap: 1000 Vision API calls/month (free tier limit)
- Safety buffer: stops at 950, keeps 50 spare for new circulars
- Before every Vision call: checks remaining budget
- If cap reached: falls back to Tesseract automatically — zero API cost
- Monthly usage persisted in vision_usage.json
- Usage printed at START and END of every run
- API key from GitHub Secret GOOGLE_VISION_API_KEY — never hardcoded

Usage:
    python ocr_vision.py                    # fix all garbled Sinhala rows
    python ocr_vision.py --test 32-2025.pdf # test one PDF
    python ocr_vision.py --status           # show usage only, no processing
"""

import argparse
import base64
import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF

# ── API Keys from environment / GitHub Secrets ONLY ──────────────────────────
GOOGLE_VISION_API_KEY = os.environ.get('GOOGLE_VISION_API_KEY', '')
GROQ_API_KEY          = os.environ.get('GROQ_API_KEY', '')

DB_FILE      = 'circulars.db'
DOWNLOAD_DIR = Path('downloads')
TEXT_DIR     = Path('extracted_text')
USAGE_FILE   = Path('vision_usage.json')   # persists monthly counter

MONTHLY_CAP    = 1000   # Google free tier limit (pages/month)
SAFETY_BUFFER  = 50     # reserve 50 pages for new circulars each month
EFFECTIVE_CAP  = MONTHLY_CAP - SAFETY_BUFFER   # hard stop at 950

SINHALA_RE        = re.compile(r'[\u0d80-\u0dff]')
MIN_SINHALA_RATIO = 0.05


# ═════════════════════════════════════════════════════════════════════════════
# MONTHLY USAGE TRACKER
# Persists in vision_usage.json — commit this file to your repo so the
# count survives across GitHub Actions runs.
# ═════════════════════════════════════════════════════════════════════════════

def load_usage() -> dict:
    """Load usage. Auto-resets when the month changes."""
    current_month = datetime.now().strftime('%Y-%m')
    if USAGE_FILE.exists():
        try:
            data = json.loads(USAGE_FILE.read_text(encoding='utf-8'))
            if data.get('month') == current_month:
                return data
        except Exception:
            pass
    # New month or corrupt file — start fresh
    return {'month': current_month, 'pages_used': 0,
            'last_updated': datetime.now().isoformat()}


def save_usage(usage: dict):
    usage['last_updated'] = datetime.now().isoformat()
    USAGE_FILE.write_text(json.dumps(usage, indent=2), encoding='utf-8')


def can_use_vision(usage: dict) -> bool:
    return (EFFECTIVE_CAP - usage['pages_used']) > 0


def increment_usage(usage: dict):
    """Call this ONLY after a confirmed successful Vision API call."""
    usage['pages_used'] += 1
    save_usage(usage)


def print_usage_status(usage: dict, label: str = ''):
    used      = usage['pages_used']
    remaining = max(EFFECTIVE_CAP - used, 0)
    bar_n     = int(used * 30 / MONTHLY_CAP)
    bar       = '█' * bar_n + '░' * (30 - bar_n)
    print(f'\n{"─"*60}')
    if label:
        print(f'  📊 Vision API Usage — {label}')
    print(f'  Month          : {usage["month"]}')
    print(f'  Used           : {used} pages')
    print(f'  Effective cap  : {EFFECTIVE_CAP}  (free tier {MONTHLY_CAP} - buffer {SAFETY_BUFFER})')
    print(f'  Remaining      : {remaining} pages')
    print(f'  [{bar}] {used}/{MONTHLY_CAP}')
    if remaining <= 0:
        print(f'  ⛔ CAP REACHED — all calls will use Tesseract fallback')
    elif remaining <= 100:
        print(f'  ⚠️  Running low — {remaining} pages left')
    else:
        print(f'  ✅ Within free tier — safe to use')
    print(f'{"─"*60}\n')


# ═════════════════════════════════════════════════════════════════════════════
# TESSERACT FALLBACK
# ═════════════════════════════════════════════════════════════════════════════

def tesseract_ocr_page(page) -> str:
    """Fallback when Vision cap is hit. Zero API cost."""
    try:
        import pytesseract
        from PIL import Image
        import io
        mat = fitz.Matrix(200 / 72, 200 / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes('png')))
        return pytesseract.image_to_string(img, lang='sin')
    except ImportError:
        return ''
    except Exception as e:
        print(f'      ⚠️  Tesseract error: {e}')
        return ''


# ═════════════════════════════════════════════════════════════════════════════
# GOOGLE VISION OCR  (with cap check)
# ═════════════════════════════════════════════════════════════════════════════

def ocr_page(page, usage: dict) -> tuple:
    """
    OCR one page with cap enforcement:
      remaining > 0  → Google Vision API  (best quality)
      remaining <= 0 → Tesseract fallback (free, always available)
    Returns: (text, method)  method = 'vision' | 'tesseract' | 'native'
    """
    remaining = EFFECTIVE_CAP - usage['pages_used']

    # ── Cap check BEFORE any API call ────────────────────────────────────────
    if remaining <= 0:
        print(f'      ⛔ Vision cap reached ({usage["pages_used"]}/{EFFECTIVE_CAP}) → Tesseract')
        return tesseract_ocr_page(page), 'tesseract'

    if not GOOGLE_VISION_API_KEY:
        print(f'      ⚠️  GOOGLE_VISION_API_KEY not set → Tesseract')
        return tesseract_ocr_page(page), 'tesseract'

    # ── Call Vision API ───────────────────────────────────────────────────────
    mat       = fitz.Matrix(200 / 72, 200 / 72)
    pix       = page.get_pixmap(matrix=mat)
    b64_image = base64.b64encode(pix.tobytes('png')).decode('utf-8')

    body = json.dumps({
        'requests': [{
            'image'       : {'content': b64_image},
            'features'    : [{'type': 'DOCUMENT_TEXT_DETECTION'}],
            'imageContext': {'languageHints': ['si', 'en']}
        }]
    }).encode('utf-8')

    url = f'https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}'
    req = urllib.request.Request(url, data=body,
                                  headers={'Content-Type': 'application/json'},
                                  method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        text = result.get('responses', [{}])[0]\
                     .get('fullTextAnnotation', {})\
                     .get('text', '')
        # Only increment AFTER confirmed success
        increment_usage(usage)
        return text, 'vision'

    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8')
        print(f'      ❌ Vision {e.code}: {err[:150]} → Tesseract')
        return tesseract_ocr_page(page), 'tesseract'
    except Exception as e:
        print(f'      ❌ Vision error: {e} → Tesseract')
        return tesseract_ocr_page(page), 'tesseract'


def extract_text(pdf_path: Path, usage: dict) -> tuple:
    """
    Extract text from every page of a PDF.
    Per page:
      Good native Sinhala → use it, no API call
      Needs OCR          → ocr_page() with cap enforcement
    Returns: (full_text, stats)
    """
    doc   = fitz.open(pdf_path)
    parts = []
    stats = {'native': 0, 'vision': 0, 'tesseract': 0}

    for i, page in enumerate(doc):
        native   = page.get_text().strip()
        si_chars = len(SINHALA_RE.findall(native))
        ratio    = si_chars / len(native) if native else 0

        if len(native) > 100 and ratio >= MIN_SINHALA_RATIO:
            parts.append(f'\n--- Page {i+1} [native] ---\n{native}')
            stats['native'] += 1
            print(f'      Page {i+1}: native ({len(native)} chars, {si_chars} SI)')
        else:
            remaining = max(EFFECTIVE_CAP - usage['pages_used'], 0)
            print(f'      Page {i+1}: OCR needed  [Vision remaining: {remaining}]...', end=' ')
            text, method = ocr_page(page, usage)
            si_v = len(SINHALA_RE.findall(text))
            parts.append(f'\n--- Page {i+1} [{method}] ---\n{text}')
            stats[method] += 1
            print(f'{len(text)} chars, {si_v} SI [{method}]')
            time.sleep(0.2)

    doc.close()
    return ''.join(parts), stats


# ═════════════════════════════════════════════════════════════════════════════
# GARBLED DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def is_garbled(text: str) -> bool:
    if not text:
        return False
    words     = text.split()
    single_si = sum(1 for w in words if len(w) == 1 and SINHALA_RE.match(w))
    return (single_si / len(words) > 0.25) if words else False


def get_garbled_rows() -> list:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT circular_number, issued_date, topic, summary, pdf_path "
        "FROM circulars WHERE language='S'"
    ).fetchall()
    conn.close()
    result = []
    for number, date, topic, summary, pdf_path in rows:
        combined = (topic or '') + ' ' + (summary or '')
        if len(SINHALA_RE.findall(combined)) > 0 and is_garbled(combined):
            words     = combined.split()
            single_si = sum(1 for w in words if len(w) == 1 and SINHALA_RE.match(w))
            result.append({
                'number'  : number, 'date': date or '',
                'topic'   : topic or '', 'pdf_path': pdf_path or '',
                'garbled_ratio': round(single_si / len(words), 2) if words else 0,
            })
    return result


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def find_pdf(row: dict) -> Path | None:
    if row['pdf_path']:
        p = Path(row['pdf_path'].replace('\\', '/'))
        if p.exists():
            return p
    safe_num   = ''.join(c for c in row['number'].replace('/', '-') if c not in ':*?"<>|').strip()
    year_match = re.search(r'(20\d\d)', row['number'])
    year       = year_match.group(1) if year_match else None
    for d in DOWNLOAD_DIR.rglob('Sinhala'):
        for v in [safe_num + '.pdf', re.sub(r'\s*\(.*?\)', '', safe_num).strip() + '.pdf']:
            p = d / v
            if p.exists():
                return p
    prefix = row['number'].split('/')[0].strip()
    for pdf in DOWNLOAD_DIR.rglob('Sinhala/*.pdf'):
        if pdf.stem.startswith(prefix + '-') and (not year or year in pdf.stem):
            return pdf
    return None


def save_txt(number: str, year: str, text: str):
    safe_num = ''.join(c for c in number.replace('/', '-') if c not in ':*?"<>|').strip()
    folder   = TEXT_DIR / year / 'Sinhala'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{safe_num}.txt').write_text(text, encoding='utf-8')


def update_db(number: str, topic: str, summary: str):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "UPDATE circulars SET topic=?, summary=? WHERE circular_number=? AND language='S'",
        (topic, summary, number)
    )
    conn.commit()
    conn.close()


def resummarise_with_groq(number: str, date: str, text: str) -> dict | None:
    if not GROQ_API_KEY:
        print('      ⚠️  GROQ_API_KEY not set')
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        resp   = client.chat.completions.create(
            model='llama-3.1-8b-instant',
            messages=[{'role': 'user', 'content':
                f"Sri Lanka government circular in Sinhala. Reply Sinhala ONLY.\n"
                f"TOPIC: (title)\nSUMMARY: (2-3 sentences)\n\n"
                f"Circular: {number} | Date: {date}\nText:\n{text[:600]}"}],
            max_tokens=300, timeout=30,
        )
        result = {}
        for line in resp.choices[0].message.content.strip().splitlines():
            if ':' in line:
                k, _, v = line.partition(':')
                k = k.strip().upper()
                if k == 'TOPIC':
                    result['topic'] = v.strip()
                elif k == 'SUMMARY':
                    result['summary'] = v.strip()
        return result
    except Exception as e:
        print(f'      ⚠️  Groq error: {e}')
        return None


# ═════════════════════════════════════════════════════════════════════════════
# TEST MODE
# ═════════════════════════════════════════════════════════════════════════════

def test_one_pdf(pdf_name: str, usage: dict):
    matches  = list(DOWNLOAD_DIR.rglob(f'*{Path(pdf_name).name}'))
    pdf_path = matches[0] if matches else Path(pdf_name)
    if not pdf_path.exists():
        print(f'❌ Not found: {pdf_name}')
        return

    print(f'\n── Vision OCR Test: {pdf_path} ──\n')
    text, stats = extract_text(pdf_path, usage)
    si_chars    = len(SINHALA_RE.findall(text))

    print(f'\n{"─"*50}')
    print(f'Total chars  : {len(text)}')
    print(f'Sinhala chars: {si_chars}')
    print(f'Pages → native:{stats["native"]}  vision:{stats["vision"]}  tesseract:{stats["tesseract"]}')
    print(f'Garbled      : {is_garbled(text[:300])}')
    print(f'{"─"*50}')
    print('\nExtracted text (first 600 chars):')
    print(text[:600])
    if si_chars > 20 and not is_garbled(text[:300]):
        print('\n✅ Clean Sinhala!')
    elif si_chars > 20:
        print('\n⚠️  Sinhala found but still garbled')
    else:
        print('\n❌ Low Sinhala — check API key and billing')


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    usage = load_usage()

    print('\n' + '='*60)
    print('  Google Vision OCR — Sinhala Garbled Text Fixer')
    print('='*60)
    print_usage_status(usage, label='START OF RUN')

    garbled = get_garbled_rows()
    print(f'Found {len(garbled)} garbled rows\n')

    if not garbled:
        print('✅ No garbled text found!')
        return

    print('Rows to fix:')
    for r in garbled:
        print(f'  {r["number"]:20s}  {r["garbled_ratio"]:.0%} garbled  "{r["topic"][:45]}"')
    print()

    fixed = skipped = failed = 0
    vision_this_run = 0

    for i, row in enumerate(garbled, 1):
        print(f'[{i}/{len(garbled)}] {row["number"]}  '
              f'[Vision remaining: {max(EFFECTIVE_CAP - usage["pages_used"], 0)}]')

        pdf_path = find_pdf(row)
        if not pdf_path:
            print('  ❌ No PDF — skipping')
            skipped += 1
            continue

        print(f'  📄 {pdf_path}')
        pages_before = usage['pages_used']

        try:
            text, stats = extract_text(pdf_path, usage)
        except Exception as e:
            print(f'  ❌ {e}')
            failed += 1
            continue

        vision_this_run += stats.get('vision', 0)
        si_chars = len(SINHALA_RE.findall(text))
        print(f'  {len(text)} chars, {si_chars} SI  '
              f'[native:{stats["native"]} vision:{stats["vision"]} tess:{stats["tesseract"]}]')

        if si_chars < 20:
            print('  ⚠️  Not enough Sinhala — skipping')
            failed += 1
            continue

        year = re.search(r'(20\d\d)', row['number'])
        save_txt(row['number'], year.group(1) if year else '2025', text)

        print('  Groq re-summarise...')
        summary = resummarise_with_groq(row['number'], row['date'], text)
        time.sleep(12)

        if summary and summary.get('topic'):
            topic  = summary.get('topic', '')
            summ   = summary.get('summary', '')
            si_new = len(SINHALA_RE.findall(topic + summ))
            update_db(row['number'], topic, summ)
            if si_new > 5 and not is_garbled(topic):
                print(f'  ✅ {topic[:60]}')
                fixed += 1
            else:
                print(f'  ⚠️  Still garbled: {topic[:60]}')
                failed += 1
        else:
            failed += 1

    # ── Show usage at END ──
    print_usage_status(usage, label='END OF RUN')

    print(f'{"="*60}')
    print(f'  Fixed              : {fixed}')
    print(f'  Skipped            : {skipped}')
    print(f'  Failed             : {failed}')
    print(f'  Vision pages used  : {vision_this_run} this run')
    print(f'  Monthly total      : {usage["pages_used"]} / {MONTHLY_CAP}')
    print(f'  Remaining free     : {max(EFFECTIVE_CAP - usage["pages_used"], 0)}')
    print(f'{"="*60}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--test',   metavar='PDF', help='Test on one PDF')
    parser.add_argument('--status', action='store_true', help='Show usage only')
    args   = parser.parse_args()
    usage  = load_usage()

    if args.status:
        print_usage_status(usage, label='CURRENT STATUS')
    elif args.test:
        print_usage_status(usage, label='BEFORE TEST')
        test_one_pdf(args.test, usage)
        print_usage_status(usage, label='AFTER TEST')
    else:
        main()
