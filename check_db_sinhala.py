import sqlite3, re

SINHALA_RE = re.compile(r'[\u0d80-\u0dff]')
conn = sqlite3.connect('circulars.db')
rows = conn.execute("SELECT circular_number, topic, summary FROM circulars WHERE language='S'").fetchall()
conn.close()

print(f"Total Sinhala rows in DB: {len(rows)}\n")

for number, topic, summary in rows[:10]:
    si_chars = len(SINHALA_RE.findall((topic or '') + (summary or '')))
    status = 'SI_OK' if si_chars > 5 else 'ENGLISH'
    print(f'{status}  {number:20s}  {(topic or "")[:50]}')
