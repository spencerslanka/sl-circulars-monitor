"""
run_pipeline.py — Daily Sri Lanka Circulars Pipeline
Runs on GitHub Actions. Only processes NEW circulars not already in the DB.
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

# ── CONFIGURATION (from GitHub Secrets) ──────────────────────────────────
GROQ_API_KEY     = os.environ['GROQ_API_KEY']
GMAIL_USER       = os.environ.get('GMAIL_USER', '')
GMAIL_PASSWORD   = os.environ.get('GMAIL_APP_PASSWORD', '')
GMAIL_TO         = os.environ.get('GMAIL_TO', '')
SLACK_WEBHOOK    = os.environ.get('SLACK_WEBHOOK_URL', '')

MODEL_NAME       = 'llama-3.1-8b-instant'      # fastest on free tier
BASE_URL         = 'https://pubad.gov.lk'
TARGET_YEARS     = {'2025', '2026'}
LANGUAGES        = ['E', 'S']
DOWNLOAD_DIR     = Path('downloads')
TEXT_DIR         = Path('extracted_text')
DB_FILE          = 'circulars.db'
MAX_TEXT_CHARS   = 1500   # 1500 chars ≈ ~500 tokens — safe for Groq free tier 6k TPM limit
DELAY_SECONDS    = 2
MIN_CHARS_PAGE   = 50

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}

client = Groq(api_key=GROQ_API_KEY)
DATE_PATTERN = re.compile(r'^(20\d{2})-(\d{2})-(\d{2})$')

# ── COUNTERS ──────────────────────────────────────────────────────────────
stats = {
    'new_found': 0,
    'downloaded': 0,
    'summarised': 0,
    'failed': 0,
}

# ═════════════════════════════════════════════════════════════════════════
# STEP 1 — SCRAPE: find circulars from the website
# ═════════════════════════════════════════════════════════════════════════

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
    year = m.group(1)
    a_tag = cells[1].find('a', href=True)
    detail_url = urljoin(BASE_URL, a_tag['href']) if a_tag else None
    return {
        'number': number, 'title': title,
        'date': date_str, 'year': year,
        'detail_url': detail_url,
    }

def scrape_new_circulars(known_numbers):
    """Scrape website and return only circulars not already in the DB."""
    new_circulars = []
    offset = 0

    while True:
        if offset == 0:
            url = (f'{BASE_URL}/web/index.php'
                   f'?option=com_circular&view=circulars&Itemid=176&lang=en')
        else:
            url = (f'{BASE_URL}/web/index.php'
                   f'?Itemid=176&lang=en&option=com_circular'
                   f'&view=circulars&limitstart={offset}')

        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f'  Fetch error: {e}')
            break

        soup = BeautifulSoup(r.text, 'html.parser')
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
            if row['number'] not in known_numbers:
                new_circulars.append(row)
                print(f'  🆕 [{row["date"]}] {row["number"]} — {row["title"][:50]}')

        if found_old:
            break

        offset += 10
        time.sleep(DELAY_SECONDS)

    return new_circulars


# ═════════════════════════════════════════════════════════════════════════
# STEP 2 — DOWNLOAD PDFs
# ═════════════════════════════════════════════════════════════════════════

def find_pdfs(detail_url, languages):
    try:
        r = requests.get(detail_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f'    Detail page error: {e}')
        return {}

    soup = BeautifulSoup(r.text, 'html.parser')
    result = {}
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'images/circulars/' not in href or not href.lower().endswith('.pdf'):
            continue
        full_url = urljoin(detail_url, href)
        parts = href.rstrip('/').split('/')
        lang_code = parts[-2].upper() if len(parts) >= 2 else 'UNKNOWN'
        if languages and lang_code not in languages:
            continue
        result[lang_code] = full_url
    return result

def safe_filename(number):
    s = number.replace('/', '-').replace('\\', '-')
    return ''.join(c for c in s if c not in ':*?"<>|').strip()

def build_pdf_path(circular, lang_code):
    lang_folder = {'E': 'English', 'S': 'Sinhala', 'T': 'Tamil'}.get(lang_code, lang_code)
    folder = DOWNLOAD_DIR / circular['year'] / lang_folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{safe_filename(circular["number"])}.pdf'

def download_pdf(url, path):
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f'    ✅ Downloaded: {path.name} ({path.stat().st_size//1024} KB)')
        return True
    except Exception as e:
        print(f'    ❌ Download failed: {e}')
        return False


# ═════════════════════════════════════════════════════════════════════════
# STEP 3 — EXTRACT TEXT
# ═════════════════════════════════════════════════════════════════════════

def build_txt_path(circular, lang_code):
    lang_folder = {'E': 'English', 'S': 'Sinhala', 'T': 'Tamil'}.get(lang_code, lang_code)
    folder = TEXT_DIR / circular['year'] / lang_folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{safe_filename(circular["number"])}.txt'

def extract_text(pdf_path, lang_code):
    """Extract text from PDF. Uses direct extraction only (OCR skipped — too slow on free CI)."""
    doc = fitz.open(pdf_path)
    full_text = ''
    total_chars = 0
    for i, page in enumerate(doc):
        text = page.get_text()
        total_chars += len(text.strip())
        full_text += f'\n--- Page {i+1} ---\n{text}'
    doc.close()
    print(f'    Extracted {total_chars} chars from {pdf_path.name}')
    return full_text


# ═════════════════════════════════════════════════════════════════════════
# STEP 4 — AI SUMMARISE
# ═════════════════════════════════════════════════════════════════════════

def build_prompt(circular, text, lang_code):
    if lang_code == 'S':
        lines = [
            "ඔබ ශ්‍රී ලංකා රජයේ චක්‍රලේඛ විශ්ලේෂණය කරන විශේෂඥයෙකි.",
            "සිංහල භාෂාවෙන් පමණක් පිළිතුරු දෙන්න.",
            "",
            "පහත JSON ආකෘතියෙන් පමණක් පිළිතුරු දෙන්න (JSON ට පෙර හෝ පසුව කිසිවක් නොලියන්න):",
            "{",
            '  "circular_number": "චක්‍රලේඛ අංකය",',
            '  "issued_date": "YYYY-MM-DD ආකෘතියෙන් දිනය, නොමැති නම් null",',
            '  "issued_by": "නිකුත් කළ අමාත්‍යාංශය හෝ අධිකාරිය",',
            '  "topic": "කෙටි මාතෘකාව සිංහලෙන්",',
            '  "summary": "චක්‍රලේඛයේ සාරාංශය සිංහලෙන් (වාක්‍ය 2-3)",',
            '  "key_instructions": ["ප්‍රධාන උපදෙස 1", "ප්‍රධාන උපදෙස 2"],',
            '  "applies_to": "අදාළ වන්නේ කාටද (සිංහලෙන්)",',
            '  "deadline": "අවසාන දිනය හෝ null",',
            '  "language_detected": "Sinhala"',
            "}",
            "",
            f"චක්‍රලේඛ අංකය: {circular['number']}",
            f"දිනය: {circular['date']}",
            f"මාතෘකාව: {circular.get('title', 'N/A')[:200]}",
            "",
            "චක්‍රලේඛ පෙළ:",
            text[:MAX_TEXT_CHARS],
        ]
    else:
        lines = [
            "You are analysing a Sri Lanka government circular.",
            "Return ONLY a valid JSON object. No text before or after. No markdown.",
            "",
            "{",
            '  "circular_number": "the official circular number",',
            '  "issued_date": "date in YYYY-MM-DD format, or null",',
            '  "issued_by": "name of the ministry or authority",',
            '  "topic": "short topic title in English",',
            '  "summary": "2-3 sentence summary in English",',
            '  "key_instructions": ["instruction 1", "instruction 2"],',
            '  "applies_to": "who this circular applies to",',
            '  "deadline": "any deadline mentioned, or null",',
            '  "language_detected": "English"',
            "}",
            "",
            f"- Number: {circular['number']}",
            f"- Date: {circular['date']}",
            f"- Title: {circular.get('title', 'N/A')[:200]}",
            "",
            f"Circular text:\n{text[:MAX_TEXT_CHARS]}",
        ]
    return "\n".join(lines)

def parse_response(text):
    text = text.strip()
    text = re.sub(r'^```json\s*', '', text)
    text = re.sub(r'^```\s*', '', text)
    text = re.sub(r'\s*```$', '', text).strip()
    if text.count('{') > text.count('}'):
        text += '}'
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)


# ═════════════════════════════════════════════════════════════════════════
# STEP 5 — DATABASE
# ═════════════════════════════════════════════════════════════════════════

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

def get_known_numbers():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT DISTINCT circular_number FROM circulars").fetchall()
    conn.close()
    return {r[0] for r in rows}

def save_to_db(circular, summary, lang_code, pdf_path, txt_path):
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''
        INSERT OR REPLACE INTO circulars
        (circular_number, issued_date, issued_by, topic, summary,
         key_instructions, applies_to, deadline, language,
         pdf_path, txt_path, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        summary.get('circular_number', circular['number']),
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
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════
# STEP 6 — ALERTS
# ═════════════════════════════════════════════════════════════════════════

def send_slack(new_circulars):
    if not SLACK_WEBHOOK:
        return
    lines = [f'📋 *{len(new_circulars)} New Sri Lanka Government Circular(s)*\n']
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
            print(f'✅ Slack alert sent (status {resp.status})')
    except Exception as e:
        print(f'❌ Slack error: {e}')

def send_email(new_circulars):
    if not GMAIL_USER or not GMAIL_PASSWORD or not GMAIL_TO:
        return
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    today = datetime.now().strftime('%Y-%m-%d')
    subject = f'🇱🇰 {len(new_circulars)} New Circular(s) — {today}'

    html_rows = ''.join([
        f"<tr><td style='padding:6px;border-bottom:1px solid #eee'>{c['number']}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{c['date']}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{c.get('topic') or c.get('title','')}</td>"
        f"<td style='padding:6px;border-bottom:1px solid #eee'>{(c.get('summary') or '')[:120]}</td></tr>"
        for c in new_circulars
    ])

    html = f"""<html><body>
    <h2>🇱🇰 New Sri Lanka Government Circulars — {today}</h2>
    <table style='border-collapse:collapse;width:100%;font-size:13px;font-family:Arial'>
        <tr style='background:#4a90d9;color:white'>
            <th style='padding:8px;text-align:left'>Number</th>
            <th style='padding:8px;text-align:left'>Date</th>
            <th style='padding:8px;text-align:left'>Topic</th>
            <th style='padding:8px;text-align:left'>Summary</th>
        </tr>
        {html_rows}
    </table>
    </body></html>"""

    msg = MIMEMultipart('alternative')
    msg['From']    = GMAIL_USER
    msg['To']      = GMAIL_TO
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.send_message(msg)
        print(f'✅ Email sent to {GMAIL_TO}')
    except Exception as e:
        print(f'❌ Email error: {e}')


# ═════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════

def main():
    print(f'\n{"="*60}')
    print(f'Sri Lanka Circulars Pipeline — {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'{"="*60}\n')

    init_db()
    known = get_known_numbers()
    print(f'Known circulars in DB: {len(known)}')

    # Step 1 — Scrape
    print('\n── STEP 1: Scraping for new circulars ──')
    new_circulars = scrape_new_circulars(known)
    print(f'New circulars found: {len(new_circulars)}')
    stats['new_found'] = len(new_circulars)

    if not new_circulars:
        print('\n✅ Nothing new today. Exiting.')
        return

    processed = []  # circulars with topic/summary for alerts

    for circular in new_circulars:
        print(f'\n── Processing: {circular["number"]} ({circular["date"]}) ──')
        circular_data = dict(circular)

        # Step 2 — Download PDFs
        print('  Downloading PDFs...')
        if not circular.get('detail_url'):
            print('  No detail URL — skipping')
            continue

        pdfs = find_pdfs(circular['detail_url'], LANGUAGES)
        if not pdfs:
            print('  No PDFs found')
            continue

        pdf_paths = {}
        for lang, url in pdfs.items():
            path = build_pdf_path(circular, lang)
            if not path.exists():
                ok = download_pdf(url, path)
                if ok:
                    pdf_paths[lang] = path
                    stats['downloaded'] += 1
            else:
                pdf_paths[lang] = path

        time.sleep(1)

        # Step 3 + 4 — Extract text & summarise
        for lang_code in LANGUAGES:
            pdf_path = pdf_paths.get(lang_code)
            if not pdf_path or not pdf_path.exists():
                continue

            print(f'  [{lang_code}] Extracting text...')
            try:
                text = extract_text(pdf_path, lang_code)
            except Exception as e:
                print(f'  [{lang_code}] Text extraction failed: {e}')
                stats['failed'] += 1
                continue

            txt_path = build_txt_path(circular, lang_code)
            txt_path.write_text(text, encoding='utf-8')

            print(f'  [{lang_code}] Summarising with Groq...')
            try:
                prompt = build_prompt(circular, text, lang_code)

                # Retry up to 3 times on rate limit
                summary = None
                for attempt in range(3):
                    try:
                        response = client.chat.completions.create(
                            model=MODEL_NAME,
                            messages=[{'role': 'user', 'content': prompt}],
                            max_tokens=400,   # limit output tokens too
                            timeout=30
                        )
                        summary = parse_response(response.choices[0].message.content)
                        break
                    except Exception as ex:
                        err = str(ex)
                        if '413' in err or 'rate_limit' in err or '429' in err:
                            wait = 20 * (attempt + 1)
                            print(f'  [{lang_code}] Rate limit — waiting {wait}s (attempt {attempt+1}/3)')
                            time.sleep(wait)
                        else:
                            raise

                if not summary:
                    raise Exception('Failed after 3 retry attempts')

                save_to_db(circular, summary, lang_code, pdf_path, txt_path)
                print(f'  [{lang_code}] ✅ {summary.get("topic", "")[:60]}')
                stats['summarised'] += 1

                # Attach English summary to circular for alert
                if lang_code == 'E':
                    circular_data['topic']   = summary.get('topic')
                    circular_data['summary'] = summary.get('summary')

                time.sleep(10)  # 10s between calls to stay within TPM limit

            except Exception as e:
                print(f'  [{lang_code}] ❌ Summarise failed: {e}')
                stats['failed'] += 1
                time.sleep(5)

        processed.append(circular_data)

    # Step 5 — Alerts
    if processed:
        print(f'\n── STEP 5: Sending alerts ({len(processed)} new circulars) ──')
        send_slack(processed)
        send_email(processed)

    # Summary
    print(f'\n{"="*60}')
    print('✅ Pipeline complete!')
    print(f'   New found   : {stats["new_found"]}')
    print(f'   Downloaded  : {stats["downloaded"]} PDFs')
    print(f'   Summarised  : {stats["summarised"]}')
    print(f'   Failed      : {stats["failed"]}')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
