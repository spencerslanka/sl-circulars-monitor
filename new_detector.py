"""
new_detector.py — Week 8: New Circular Detector
=================================================
Compares what is on the website RIGHT NOW against what is in the DB.
Produces a structured report of:
  - Genuinely new circulars (never seen before)
  - Missing-language versions (EN stored but SI missing, or vice versa)
  - Circulars whose titles changed since last scrape (possible amendment)

Run manually or add as its own GitHub Actions step:
    python new_detector.py

Output:
  - Prints a summary to stdout (visible in Actions logs)
  - Writes  new_circulars_report.json   for downstream use
  - Optionally sends a Slack / email digest

Why this is useful vs just run_pipeline.py:
  run_pipeline.py  — processes new circulars end-to-end (heavy, 10s delays)
  new_detector.py  — lightweight SCAN ONLY, fast, no AI calls, no downloads
                     Run this first to see what changed before doing the
                     full pipeline run.  Good for debugging and reporting.
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL      = 'https://pubad.gov.lk'
TARGET_YEARS  = {'2025', '2026'}
DB_FILE       = 'circulars.db'
REPORT_FILE   = 'new_circulars_report.json'
SLACK_WEBHOOK = os.environ.get('SLACK_WEBHOOK_URL', '')
DELAY         = 1   # seconds between page requests

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}
DATE_PATTERN = re.compile(r'^(20\d{2})-(\d{2})-(\d{2})$')


# ── Scrape ALL circulars in target years from the website ─────────────────────

def scrape_all_circulars() -> list[dict]:
    """
    Scrape every circular in TARGET_YEARS from the website.
    Returns a list of dicts: {number, title, date, year, detail_url}
    """
    found  = []
    offset = 0

    print('🌐 Scanning website...')
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
            print(f'  ⚠️  Page fetch error (offset {offset}): {e}')
            break

        soup   = BeautifulSoup(r.text, 'html.parser')
        tables = soup.find_all('table')
        if len(tables) < 2:
            break

        rows       = tables[1].find_all('tr')
        found_old  = False
        page_count = 0

        for tr in rows:
            cells = tr.find_all('td')
            if len(cells) < 3:
                continue
            number   = cells[0].get_text(strip=True)
            title    = cells[1].get_text(strip=True)
            date_str = cells[2].get_text(strip=True)
            m = DATE_PATTERN.match(date_str)
            if not m:
                continue
            year = m.group(1)
            if year not in TARGET_YEARS:
                found_old = True
                continue
            a_tag      = cells[1].find('a', href=True)
            detail_url = urljoin(BASE_URL, a_tag['href']) if a_tag else None
            found.append({
                'number': number, 'title': title,
                'date': date_str, 'year': year,
                'detail_url': detail_url,
            })
            page_count += 1

        print(f'  Page offset={offset}: found {page_count} target-year rows')
        if found_old:
            break
        offset += 10
        time.sleep(DELAY)

    print(f'  Total on website: {len(found)} circulars in {TARGET_YEARS}')
    return found


# ── Load what we already know from the DB ─────────────────────────────────────

def load_db_state() -> dict:
    """
    Returns a dict keyed by circular_number:
    {
      '10/2025': {
          'languages': {'E', 'S'},
          'topic_en': '...',
          'processed_at': '...',
      },
      ...
    }
    """
    if not Path(DB_FILE).exists():
        print(f'  ⚠️  {DB_FILE} not found — treating everything as new')
        return {}

    conn  = sqlite3.connect(DB_FILE)
    rows  = conn.execute(
        'SELECT circular_number, language, topic, processed_at FROM circulars'
    ).fetchall()
    conn.close()

    state = {}
    for number, lang, topic, processed_at in rows:
        if number not in state:
            state[number] = {'languages': set(), 'topic_en': None, 'processed_at': processed_at}
        state[number]['languages'].add(lang)
        if lang == 'E' and topic:
            state[number]['topic_en'] = topic
    return state


# ── Compare website vs DB ─────────────────────────────────────────────────────

def detect_changes(website: list[dict], db_state: dict) -> dict:
    """
    Classify each website circular into one of three buckets:
      new          — circular_number never seen in DB
      missing_lang — in DB but one language version is absent
      title_change — title on website differs from stored topic (possible amendment)
    """
    truly_new    = []
    missing_lang = []
    title_change = []
    up_to_date   = []

    for c in website:
        num = c['number']
        if num not in db_state:
            truly_new.append(c)
        else:
            stored = db_state[num]
            missing = []
            if 'E' not in stored['languages']:
                missing.append('E')
            if 'S' not in stored['languages']:
                missing.append('S')
            if missing:
                c['missing_languages'] = missing
                missing_lang.append(c)
            # Check for title change (website title vs stored EN topic)
            stored_topic = (stored.get('topic_en') or '').lower().strip()
            site_title   = c['title'].lower().strip()
            if stored_topic and site_title and stored_topic not in site_title and site_title not in stored_topic:
                c['stored_topic'] = stored.get('topic_en', '')
                title_change.append(c)
            elif not missing:
                up_to_date.append(c)

    return {
        'new'         : truly_new,
        'missing_lang': missing_lang,
        'title_change': title_change,
        'up_to_date'  : up_to_date,
    }


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(changes: dict, scanned_at: str):
    n_new  = len(changes['new'])
    n_miss = len(changes['missing_lang'])
    n_chg  = len(changes['title_change'])
    n_ok   = len(changes['up_to_date'])
    total  = n_new + n_miss + n_chg + n_ok

    print(f'\n{"═"*60}')
    print(f'  NEW CIRCULAR DETECTOR REPORT  —  {scanned_at}')
    print(f'{"═"*60}')
    print(f'  Website total  : {total}')
    print(f'  ✅ Up to date  : {n_ok}')
    print(f'  🆕 New         : {n_new}')
    print(f'  ⚠️  Missing lang: {n_miss}')
    print(f'  📝 Title change: {n_chg}')

    if changes['new']:
        print(f'\n{"─"*60}')
        print('  🆕 TRULY NEW CIRCULARS')
        print(f'{"─"*60}')
        for c in changes['new']:
            print(f'  [{c["date"]}]  {c["number"]:20s}  {c["title"][:55]}')

    if changes['missing_lang']:
        print(f'\n{"─"*60}')
        print('  ⚠️  MISSING LANGUAGE VERSIONS')
        print(f'{"─"*60}')
        for c in changes['missing_lang']:
            missing = ', '.join(c.get('missing_languages', []))
            print(f'  [{c["date"]}]  {c["number"]:20s}  missing: {missing}  — {c["title"][:45]}')

    if changes['title_change']:
        print(f'\n{"─"*60}')
        print('  📝 POSSIBLE AMENDMENTS (title changed)')
        print(f'{"─"*60}')
        for c in changes['title_change']:
            print(f'  {c["number"]:20s}')
            print(f'    Website : {c["title"][:70]}')
            print(f'    DB      : {c.get("stored_topic","")[:70]}')

    print(f'\n{"═"*60}\n')


def save_report(changes: dict, scanned_at: str):
    report = {
        'scanned_at'       : scanned_at,
        'summary': {
            'new'          : len(changes['new']),
            'missing_lang' : len(changes['missing_lang']),
            'title_change' : len(changes['title_change']),
            'up_to_date'   : len(changes['up_to_date']),
        },
        'new'          : changes['new'],
        'missing_lang' : changes['missing_lang'],
        'title_change' : changes['title_change'],
    }
    Path(REPORT_FILE).write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    print(f'📄 Report saved → {REPORT_FILE}')


# ── Optional Slack notification ───────────────────────────────────────────────

def notify_slack(changes: dict, scanned_at: str):
    if not SLACK_WEBHOOK:
        return
    n_new  = len(changes['new'])
    n_miss = len(changes['missing_lang'])
    if n_new == 0 and n_miss == 0:
        return   # nothing interesting to report

    lines = [f'🇱🇰 *Circulars Scan — {scanned_at}*']
    if n_new:
        lines.append(f'\n*🆕 {n_new} New Circular(s):*')
        for c in changes['new']:
            lines.append(f'• *{c["number"]}* ({c["date"]}) — {c["title"][:60]}')
    if n_miss:
        lines.append(f'\n*⚠️ {n_miss} Missing Language Version(s):*')
        for c in changes['missing_lang']:
            missing = ', '.join(c.get('missing_languages', []))
            lines.append(f'• *{c["number"]}* missing {missing}')

    payload = json.dumps({'text': '\n'.join(lines)}).encode()
    try:
        import urllib.request
        req = urllib.request.Request(
            SLACK_WEBHOOK, data=payload,
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req) as resp:
            print(f'✅ Slack notification sent (HTTP {resp.status})')
    except Exception as e:
        print(f'❌ Slack error: {e}')


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    scanned_at = datetime.now().strftime('%Y-%m-%d %H:%M UTC')
    print(f'\nSri Lanka Circulars — New Detector')
    print(f'Scan started: {scanned_at}\n')

    website_circulars = scrape_all_circulars()
    db_state          = load_db_state()

    print(f'\n📊 DB state: {len(db_state)} unique circular numbers stored')

    changes = detect_changes(website_circulars, db_state)
    print_report(changes, scanned_at)
    save_report(changes, scanned_at)
    notify_slack(changes, scanned_at)

    # Exit code 1 if new circulars found — useful for GitHub Actions conditionals
    n_actionable = len(changes['new']) + len(changes['missing_lang'])
    if n_actionable:
        print(f'ℹ️  {n_actionable} actionable item(s) found.')
        print('   Run python run_pipeline.py to process them.\n')
        exit(1)   # non-zero → downstream steps can check $?
    else:
        print('✅ Nothing new. DB is up to date.\n')


if __name__ == '__main__':
    main()
