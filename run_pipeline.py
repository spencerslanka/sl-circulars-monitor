"""
run_pipeline.py — Sri Lanka Circulars Pipeline
================================================
Runs on GitHub Actions (daily schedule).
Only processes NEW circulars not already in the DB.

Fixes in this version:
  ✅ Sinhala OCR re-enabled (fast path: direct text first, OCR fallback per page)
  ✅ Separate token limits for English vs Sinhala (Sinhala tokenises 2-3x heavier)
  ✅ Alerts called HERE only — duplicate removed from weekly_alerts.yml
  ✅ NEW detection uses (number, language) pairs — catches missing-language cases
  ✅ Cleaner error handling and per-step logging
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import sqlite3
import time
import re
import os
import fitz
from pathlib import Path
from datetime import datetime
from groq import Groq

# ── CONFIGURATION (from GitHub Secrets) ───────────────────────────────────────
GROQ_API_KEY   = os.environ['GROQ_API_KEY']
GMAIL_USER     = os.environ.get('GMAIL_USER', '')
GMAIL_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
GMAIL_TO       = os.environ.get('GMAIL_TO', '')
SLACK_WEBHOOK  = os.environ.get('SLACK_WEBHOOK_URL', '')

MODEL_NAME     = 'llama-3.1-8b-instant'
BASE_URL       = 'https://pubad.gov.lk'
TARGET_YEARS   = {'2025', '2026'}
LANGUAGES      = ['E', 'S']
DOWNLOAD_DIR   = Path('downloads')
TEXT_DIR       = Path('extracted_text')
DB_FILE        = 'circulars.db'
DELAY_SECONDS  = 2

# ── TOKEN LIMITS ───────────────────────────────────────────────────────────────
# Groq free tier: 6000 tokens/min.
# Sinhala Unicode chars cost ~2-3x more tokens than ASCII.
# Keep both well under ~500 output tokens to stay safe.
MAX_TEXT_CHARS_EN = 1500   # ~500 tokens for English
MAX_TEXT_CHARS_SI = 600    # ~500 tokens for Sinhala (fewer chars, same token budget)

# Pages with fewer than this many native chars are treated as scanned → OCR
MIN_CHARS_PAGE = 50

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}

client       = Groq(api_key=GROQ_API_KEY)
DATE_PATTERN = re.compile(r'^(20\d{2})-(\d{2})-(\d{2})$')

stats = {'new_found': 0, 'downloaded': 0, 'summarised': 0, 'failed': 0, 'ocr_used': 0}


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — SCRAPE
# ═════════════════════════════════════════════════════════════════════════════

def parse_row(row):
    cells = row.find_all('td')
    if len(cells) < 3:
        return None
    number   = cells[0].get_text(strip=True)
    title    = cells[1].get_text(strip=True)
    date_str = cells[2].get_text(strip=True)
    m = DATE_PATTERN.match(date_str)
    if not m:
        return None
    year  = m.group(1)
    a_tag = cells[1].find('a', href=True)
    detail_url = urljoin(BASE_URL, a_tag['href']) if a_tag else None
    return {'number': number, 'title': title, 'date': date_str,
            'year': year, 'detail_url': detail_url}


def scrape_new_circulars(known_pairs: set) -> list:
    """
    Scrape website. Return only circulars where (number, lang) pair
    is missing from the DB — so a circular re-runs if e.g. only the
    English version was stored but Sinhala is missing.
    """
    new_circulars = []
    offset = 0

    while True:
        url = (
            f'{BASE_URL}/web/index.php?option=com_circular&view=circulars'
            f'&Itemid=176&lang=en'
            + (f'&limitstart={offset}' if offset else '')
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f'  ⚠️  Fetch error at offset {offset}: {e}')
            break

        soup   = BeautifulSoup(r.text, 'html.parser')
        tables = soup.find_all('table')
        if len(tables) < 2:
            break

        rows = [parse_row(tr) for tr in tables[1].find_all('tr')]
        rows = [r for r in rows if r]
        if not rows:
            break

        found_old = False
        for row in rows:
            if row['year'] not in TARGET_YEARS:
                found_old = True
                continue
            needs_en = (row['number'], 'E') not in known_pairs
            needs_si = (row['number'], 'S') not in known_pairs
            if needs_en or needs_si:
                row['needs_en'] = needs_en
                row['needs_si'] = needs_si
                new_circulars.append(row)
                print(
                    f'  🆕 [{row["date"]}] {row["number"]} — {row["title"][:50]}'
                    f'  (EN:{needs_en} SI:{needs_si})'
                )

        if found_old:
            break
        offset += 10
        time.sleep(DELAY_SECONDS)

    return new_circulars


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — DOWNLOAD PDFs
# ═════════════════════════════════════════════════════════════════════════════

def find_pdfs(detail_url: str, languages: list) -> dict:
    try:
        r = requests.get(detail_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f'    ⚠️  Detail page error: {e}')
        return {}
    soup   = BeautifulSoup(r.text, 'html.parser')
    result = {}
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'images/circulars/' not in href or not href.lower().endswith('.pdf'):
            continue
        full_url  = urljoin(detail_url, href)
        parts     = href.rstrip('/').split('/')
        lang_code = parts[-2].upper() if len(parts) >= 2 else 'UNKNOWN'
        if languages and lang_code not in languages:
            continue
        result[lang_code] = full_url
    return result


def safe_filename(number: str) -> str:
    return ''.join(
        c for c in number.replace('/', '-').replace('\\', '-')
        if c not in ':*?"<>|'
    ).strip()


def build_pdf_path(circular: dict, lang_code: str) -> Path:
    folder = DOWNLOAD_DIR / circular['year'] / {'E': 'English', 'S': 'Sinhala'}.get(lang_code, lang_code)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{safe_filename(circular["number"])}.pdf'


def download_pdf(url: str, path: Path) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f'    ✅ Downloaded: {path.name} ({path.stat().st_size // 1024} KB)')
        return True
    except Exception as e:
        print(f'    ❌ Download failed: {e}')
        return False


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — TEXT EXTRACTION  (native text first, OCR fallback per page)
# ═════════════════════════════════════════════════════════════════════════════

def _ocr_page(page, lang_code: str) -> str:
    """
    Render a single PyMuPDF page to an image and run Tesseract OCR.
    - lang_code 'S' → tesseract language 'sin'
    - lang_code 'E' → tesseract language 'eng'
    Requires: tesseract-ocr + tesseract-ocr-sin installed on the runner.
    The GitHub Actions workflow already installs both.
    """
    try:
        import pytesseract
        from PIL import Image
        import io

        tess_lang = 'sin' if lang_code == 'S' else 'eng'
        # 200 DPI gives good OCR quality at acceptable speed
        mat = fitz.Matrix(200 / 72, 200 / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img = Image.open(io.BytesIO(pix.tobytes('png')))
        return pytesseract.image_to_string(img, lang=tess_lang)
    except Exception as e:
        print(f'      ⚠️  OCR error: {e}')
        return ''


def extract_text(pdf_path: Path, lang_code: str) -> tuple:
    """
    Extract text from a PDF file.
    Per page: try native PyMuPDF first; if < MIN_CHARS_PAGE chars, OCR instead.
    Returns: (full_text: str, ocr_was_used: bool)
    """
    doc      = fitz.open(pdf_path)
    parts    = []
    ocr_used = False

    for i, page in enumerate(doc):
        native = page.get_text().strip()
        if len(native) >= MIN_CHARS_PAGE:
            parts.append(f'\n--- Page {i+1} ---\n{native}')
        else:
            print(f'      🔍 Page {i+1}: only {len(native)} chars → trying OCR')
            ocr_text = _ocr_page(page, lang_code)
            parts.append(f'\n--- Page {i+1} [OCR] ---\n{ocr_text}')
            if ocr_text.strip():
                ocr_used = True

    doc.close()
    full_text = ''.join(parts)
    method    = 'OCR' if ocr_used else 'native'
    print(f'    📄 {len(full_text)} chars ({method}) — {pdf_path.name}')
    return full_text, ocr_used


def build_txt_path(circular: dict, lang_code: str) -> Path:
    folder = TEXT_DIR / circular['year'] / {'E': 'English', 'S': 'Sinhala'}.get(lang_code, lang_code)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{safe_filename(circular["number"])}.txt'


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — AI SUMMARISATION
# ═════════════════════════════════════════════════════════════════════════════

def build_prompt(circular: dict, text: str, lang_code: str) -> str:
    # Use language-appropriate token budget
    limit   = MAX_TEXT_CHARS_SI if lang_code == 'S' else MAX_TEXT_CHARS_EN
    snippet = text[:limit]

    base = (
        f"Circular: {circular['number']} | Date: {circular['date']}\n"
        f"Title: {circular.get('title', '')[:150]}\n\nText:\n{snippet}"
    )
    if lang_code == 'S':
        return f"""You are analysing a Sri Lanka government circular in Sinhala.
Extract these fields and reply in EXACT format, no extra text:

TOPIC: (short title in Sinhala)
SUMMARY: (2-3 sentences in Sinhala)
ISSUED_BY: (ministry or authority in Sinhala)
ISSUED_DATE: (YYYY-MM-DD or null)
APPLIES_TO: (who it applies to, in Sinhala)
DEADLINE: (deadline date or null)

{base}"""
    else:
        return f"""You are analysing a Sri Lanka government circular in English.
Extract these fields and reply in EXACT format, no extra text:

TOPIC: (short title)
SUMMARY: (2-3 sentences)
ISSUED_BY: (ministry or authority)
ISSUED_DATE: (YYYY-MM-DD or null)
APPLIES_TO: (who it applies to)
DEADLINE: (deadline date or null)

{base}"""


def parse_response(raw: str) -> dict:
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
    result.setdefault('key_instructions', [])
    return result


def summarise_with_groq(circular: dict, text: str, lang_code: str) -> dict | None:
    prompt = build_prompt(circular, text, lang_code)
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=400,
                timeout=30,
            )
            return parse_response(resp.choices[0].message.content)
        except Exception as ex:
            err = str(ex)
            if '413' in err or 'rate_limit' in err or '429' in err:
                wait = 20 * (attempt + 1)
                print(f'    ⏳ Rate limit — waiting {wait}s (attempt {attempt+1}/3)')
                time.sleep(wait)
            else:
                print(f'    ❌ Groq error: {ex}')
                return None
    return None


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — DATABASE
# ═════════════════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS circulars (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            circular_number  TEXT NOT NULL,
            issued_date      TEXT,
            issued_by        TEXT,
            topic            TEXT,
            summary          TEXT,
            key_instructions TEXT,
            applies_to       TEXT,
            deadline         TEXT,
            language         TEXT,
            pdf_path         TEXT,
            txt_path         TEXT,
            processed_at     TEXT,
            UNIQUE(circular_number, language)
        )
    ''')
    conn.commit()
    conn.close()


def get_known_pairs() -> set:
    """
    Return set of (circular_number, language) already stored.
    Using pairs (not just numbers) means a circular with only EN stored
    will still be picked up again to process the SI version.
    """
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT circular_number, language FROM circulars").fetchall()
    conn.close()
    return {(r[0], r[1]) for r in rows}


def save_to_db(circular: dict, summary: dict, lang_code: str,
               pdf_path: Path, txt_path: Path):
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''
        INSERT OR REPLACE INTO circulars
        (circular_number, issued_date, issued_by, topic, summary,
         key_instructions, applies_to, deadline, language,
         pdf_path, txt_path, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        circular['number'],
        summary.get('issued_date'),
        summary.get('issued_by'),
        summary.get('topic'),
        summary.get('summary'),
        json.dumps(summary.get('key_instructions', []), ensure_ascii=False),
        summary.get('applies_to'),
        summary.get('deadline'),
        lang_code,
        str(pdf_path) if pdf_path else None,
        str(txt_path) if txt_path else None,
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 — ALERTS
# NOTE: Alerts are sent HERE only.  The weekly_alerts.yml no longer
#       calls run_alerts.py in the pipeline job — that prevents duplicates.
# ═════════════════════════════════════════════════════════════════════════════

def send_slack(new_circulars: list):
    if not SLACK_WEBHOOK:
        print('  ⏭️  Slack not configured — skipping')
        return
    lines = [f'🇱🇰 *{len(new_circulars)} New Sri Lanka Government Circular(s)*\n']
    for c in new_circulars:
        topic = c.get('topic') or c.get('title', 'N/A')
        lines.append(f'• *{c["number"]}* ({c["date"]}) — {topic}')
    payload = json.dumps({'text': '\n'.join(lines)}).encode()
    try:
        import urllib.request
        req = urllib.request.Request(
            SLACK_WEBHOOK, data=payload,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as resp:
            print(f'  ✅ Slack alert sent (HTTP {resp.status})')
    except Exception as e:
        print(f'  ❌ Slack error: {e}')


def send_email(new_circulars: list):
    if not GMAIL_USER or not GMAIL_PASSWORD or not GMAIL_TO:
        print('  ⏭️  Email not configured — skipping')
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    today = datetime.now().strftime('%Y-%m-%d')
    subject = f'🇱🇰 {len(new_circulars)} New Circular(s) — {today}'

    rows_html = ''.join(
        f"<tr>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{c['number']}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{c['date']}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{c.get('topic') or c.get('title', '')}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{(c.get('summary') or '')[:120]}…</td>"
        f"</tr>"
        for c in new_circulars
    )

    html = f"""<html><body style='font-family:Arial,sans-serif;color:#1e2340'>
<h2 style='color:#c8102e'>🇱🇰 New Sri Lanka Government Circulars</h2>
<p><strong>Date:</strong> {today} &nbsp;|&nbsp;
   <strong>{len(new_circulars)}</strong> new circular(s) detected</p>
<table style='border-collapse:collapse;width:100%;font-size:13px'>
  <tr style='background:#8b0000;color:white'>
    <th style='padding:8px;text-align:left'>Number</th>
    <th style='padding:8px;text-align:left'>Date</th>
    <th style='padding:8px;text-align:left'>Topic</th>
    <th style='padding:8px;text-align:left'>Summary</th>
  </tr>
  {rows_html}
</table>
<p style='color:#888;font-size:11px;margin-top:20px'>
  Generated by Sri Lanka Circulars Monitor · GitHub Actions
</p>
</body></html>"""

    msg            = MIMEMultipart('alternative')
    msg['From']    = GMAIL_USER
    msg['To']      = GMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html'))
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.send_message(msg)
        print(f'  ✅ Email sent to {GMAIL_TO}')
    except Exception as e:
        print(f'  ❌ Email error: {e}')


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    print(f'\n{"="*62}')
    print(f'Sri Lanka Circulars Pipeline — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'{"="*62}\n')

    init_db()
    known_pairs = get_known_pairs()
    print(f'Known (number, language) pairs in DB: {len(known_pairs)}')

    print('\n── STEP 1: Scraping ──')
    new_circulars  = scrape_new_circulars(known_pairs)
    stats['new_found'] = len(new_circulars)
    print(f'New circulars: {len(new_circulars)}')

    if not new_circulars:
        print('\n✅ Nothing new today.')
        return

    processed = []

    for circular in new_circulars:
        print(f'\n── {circular["number"]} ({circular["date"]}) ──')
        circular_data = dict(circular)

        if not circular.get('detail_url'):
            print('  ⚠️  No detail URL — skipping')
            continue

        print('  Downloading PDFs...')
        pdfs = find_pdfs(circular['detail_url'], LANGUAGES)
        if not pdfs:
            print('  ⚠️  No PDFs found')
            continue

        pdf_paths = {}
        for lang, url in pdfs.items():
            path = build_pdf_path(circular, lang)
            if not path.exists():
                if download_pdf(url, path):
                    pdf_paths[lang] = path
                    stats['downloaded'] += 1
            else:
                pdf_paths[lang] = path
                print(f'    ⏭️  Already on disk: {path.name}')
        time.sleep(1)

        for lang_code in LANGUAGES:
            if (circular['number'], lang_code) in known_pairs:
                print(f'  [{lang_code}] Already in DB — skipping')
                continue

            pdf_path = pdf_paths.get(lang_code)
            if not pdf_path or not pdf_path.exists():
                print(f'  [{lang_code}] No PDF — skipping')
                continue

            print(f'  [{lang_code}] Extracting text...')
            try:
                text, ocr_used = extract_text(pdf_path, lang_code)
                if ocr_used:
                    stats['ocr_used'] += 1
            except Exception as e:
                print(f'  [{lang_code}] ❌ Extraction failed: {e}')
                stats['failed'] += 1
                continue

            build_txt_path(circular, lang_code).write_text(text, encoding='utf-8')

            if not text.strip():
                print(f'  [{lang_code}] ⚠️  Empty text — skipping AI step')
                stats['failed'] += 1
                continue

            print(f'  [{lang_code}] Summarising...')
            summary = summarise_with_groq(circular, text, lang_code)
            if summary:
                save_to_db(circular, summary, lang_code, pdf_path,
                           build_txt_path(circular, lang_code))
                print(f'  [{lang_code}] ✅ {(summary.get("topic") or "")[:60]}')
                stats['summarised'] += 1
                if lang_code == 'E':
                    circular_data['topic']   = summary.get('topic')
                    circular_data['summary'] = summary.get('summary')
            else:
                stats['failed'] += 1

            time.sleep(10)   # stay within Groq 6k TPM free tier

        processed.append(circular_data)

    if processed:
        print(f'\n── Sending alerts ({len(processed)} circular(s)) ──')
        send_slack(processed)
        send_email(processed)

    print(f'\n{"="*62}')
    print('✅ Pipeline complete!')
    print(f'   New found  : {stats["new_found"]}')
    print(f'   Downloaded : {stats["downloaded"]} PDFs')
    print(f'   OCR used   : {stats["ocr_used"]} files')
    print(f'   Summarised : {stats["summarised"]}')
    print(f'   Failed     : {stats["failed"]}')
    print(f'{"="*62}\n')


if __name__ == '__main__':
    main()
