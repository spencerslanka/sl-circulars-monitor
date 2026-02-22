"""
Sri Lanka Circulars Monitor — Weekly Alert Script
Runs automatically via GitHub Actions every week.
"""

import json
import os
import sqlite3
import smtplib
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import gspread
from google.oauth2.service_account import Credentials

# ── CONFIG FROM ENVIRONMENT VARIABLES (set in GitHub Secrets) ─────────────────
GROQ_API_KEY       = os.environ.get('GROQ_API_KEY', '')
GOOGLE_SHEET_ID    = os.environ.get('GOOGLE_SHEET_ID', '')
GOOGLE_SHEET_TAB   = os.environ.get('GOOGLE_SHEET_TAB', 'Circulars')
SLACK_WEBHOOK_URL  = os.environ.get('SLACK_WEBHOOK_URL', '')
GMAIL_USER         = os.environ.get('GMAIL_USER', '')
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')
GMAIL_TO           = os.environ.get('GMAIL_TO', '')
DB_FILE            = 'circulars.db'


# ── LOAD CIRCULARS FROM DATABASE ──────────────────────────────────────────────
def load_circulars_from_db(db_file, lang='E', limit=None):
    conn  = sqlite3.connect(db_file)
    cur   = conn.cursor()
    query = '''
        SELECT circular_number, issued_date, topic, summary,
               key_instructions, applies_to, deadline, issued_by
        FROM circulars
        WHERE language = ?
        ORDER BY issued_date DESC
    '''
    if limit:
        query += f' LIMIT {limit}'
    cur.execute(query, (lang,))
    rows = cur.fetchall()
    conn.close()

    circulars = []
    for row in rows:
        circulars.append({
            'number'          : row[0],
            'date'            : row[1],
            'topic'           : row[2],
            'summary'         : row[3],
            'key_instructions': row[4],
            'applies_to'      : row[5],
            'deadline'        : row[6],
            'issued_by'       : row[7],
        })
    return circulars


# ── GOOGLE SHEETS ─────────────────────────────────────────────────────────────
def push_to_google_sheets(circulars, sheet_id, tab_name):
    # Load credentials from environment variable
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON', '')
    if not creds_json:
        print('❌ GOOGLE_CREDENTIALS_JSON not set')
        return False

    creds_dict = json.loads(creds_json)
    scopes     = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc    = gspread.authorize(creds)

    try:
        sh = gc.open_by_url('https://docs.google.com/spreadsheets/d/' + sheet_id)
        try:
            ws = sh.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=tab_name, rows=200, cols=10)

        ws.clear()
        ws.append_row(['Number', 'Date', 'Topic', 'Summary', 'Applies To', 'Deadline', 'Issued By'])

        rows = [[
            c.get('number', ''),
            c.get('date', ''),
            c.get('topic', ''),
            c.get('summary', ''),
            c.get('applies_to', ''),
            c.get('deadline', ''),
            c.get('issued_by', ''),
        ] for c in circulars]
        ws.append_rows(rows)

        print(f'✅ {len(circulars)} rows pushed to Google Sheets')
        return True
    except Exception as e:
        print(f'❌ Google Sheets error: {e}')
        return False


# ── SLACK ─────────────────────────────────────────────────────────────────────
def send_slack_alert(webhook_url, circulars, max_items=5):
    if not webhook_url:
        print('❌ SLACK_WEBHOOK_URL not set')
        return False

    recent = circulars[:max_items]
    lines  = [
        f'📋 *Sri Lanka Government Circulars Update*',
        f'_{len(circulars)} total circulars — {datetime.now().strftime("%Y-%m-%d")}_\n'
    ]
    for c in recent:
        lines.append(f'• *{c.get("number")}* ({(c.get("date") or "")[:10]}) — {c.get("topic")}')
    if len(circulars) > max_items:
        lines.append(f'_...and {len(circulars) - max_items} more_')

    payload = json.dumps({'text': '\n'.join(lines)}).encode('utf-8')
    req     = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status == 200:
                print('✅ Slack alert sent!')
                return True
    except Exception as e:
        print(f'❌ Slack error: {e}')
    return False


# ── EMAIL ─────────────────────────────────────────────────────────────────────
def send_email_alert(gmail_user, gmail_password, to_email, circulars, max_items=10):
    if not gmail_user or not gmail_password:
        print('❌ Gmail credentials not set')
        return False

    today   = datetime.now().strftime('%Y-%m-%d')
    subject = f'🇱🇰 Circulars Update — {today} ({len(circulars)} total)'
    recent  = circulars[:max_items]

    html_rows = ''.join([
        f"""<tr>
            <td style='padding:6px;border-bottom:1px solid #eee'>{c.get('number','')}</td>
            <td style='padding:6px;border-bottom:1px solid #eee'>{(c.get('date') or '')[:10]}</td>
            <td style='padding:6px;border-bottom:1px solid #eee'>{c.get('topic','')}</td>
            <td style='padding:6px;border-bottom:1px solid #eee'>{(c.get('summary') or '')[:120]}...</td>
        </tr>""" for c in recent
    ])

    html = f"""
    <html><body>
    <h2>🇱🇰 Sri Lanka Government Circulars Update</h2>
    <p><strong>Date:</strong> {today} &nbsp;|&nbsp; <strong>Total:</strong> {len(circulars)}</p>
    <table style='border-collapse:collapse;width:100%;font-family:Arial,sans-serif;font-size:13px'>
        <tr style='background:#4a90d9;color:white'>
            <th style='padding:8px;text-align:left'>Number</th>
            <th style='padding:8px;text-align:left'>Date</th>
            <th style='padding:8px;text-align:left'>Topic</th>
            <th style='padding:8px;text-align:left'>Summary</th>
        </tr>
        {html_rows}
    </table>
    <p style='color:#888;font-size:11px'>Generated by Sri Lanka Circulars Monitor — GitHub Actions</p>
    </body></html>
    """

    msg            = MIMEMultipart('alternative')
    msg['From']    = gmail_user
    msg['To']      = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
        print(f'✅ Email sent to {to_email}')
        return True
    except Exception as e:
        print(f'❌ Email error: {e}')
        return False


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print('🚀 Sri Lanka Circulars Monitor — Weekly Run')
    print(f'   Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print('=' * 50)

    # Load circulars
    circulars_en = load_circulars_from_db(DB_FILE, lang='E')
    circulars_si = load_circulars_from_db(DB_FILE, lang='S')
    circulars    = circulars_en + circulars_si
    print(f'✅ Loaded {len(circulars)} circulars (EN: {len(circulars_en)}, SI: {len(circulars_si)})')
    print()

    # Google Sheets
    print('📊 Google Sheets...')
    push_to_google_sheets(circulars, GOOGLE_SHEET_ID, GOOGLE_SHEET_TAB)
    print()

    # Slack
    print('💬 Slack...')
    send_slack_alert(SLACK_WEBHOOK_URL, circulars)
    print()

    # Email
    print('📧 Email...')
    send_email_alert(GMAIL_USER, GMAIL_APP_PASSWORD, GMAIL_TO, circulars)
    print()

    print('=' * 50)
    print('✅ All done!')


if __name__ == '__main__':
    main()
