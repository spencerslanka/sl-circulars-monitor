"""
app.py  —  Sri Lanka Government Circulars Monitor
===================================================
Four pages:
    🏠 Home      — overview + recent + AI Q&A
    🤖 AI Q&A    — RAG-powered chat
    📋 Browse    — search & filter all circulars
    📊 Dashboard — stats, charts, deadline tracker
    ⚙️  Setup    — vector store builder

Change from previous version:
    ✅ Sinhala circulars shown FIRST throughout (most users are Sinhala)
    ✅ Default language filter set to Sinhala
    ✅ Sinhala count shown first in all stats
"""

import os
import sqlite3
import json
from pathlib import Path

import streamlit as st

st.set_page_config(
    page_title="ශ්‍රී ලංකා රජයේ චක්‍රලේඛ නිරීක්ෂණ පද්ධතිය",
    page_icon="🇱🇰",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_FILE    = "circulars.db"
CHROMA_DIR = "./chroma_db"


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Sinhala:wght@400;600;700;800&family=Lora:wght@600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

*, *::before, *::after { box-sizing: border-box; }
html, body, .stApp, [data-testid="stAppViewContainer"] {
    background: #f0f2f8 !important;
    color: #1e2340;
    font-family: 'Plus Jakarta Sans', sans-serif;
    font-size: 15px;
}
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1e2340 0%, #2a3060 100%) !important;
    border-right: none !important;
    box-shadow: 4px 0 24px rgba(0,0,0,0.18) !important;
}
section[data-testid="stSidebar"] .stRadio label {
    color: #c8d0e8 !important; font-size: 14px !important;
    font-weight: 600 !important; padding: 10px 6px !important;
}
section[data-testid="stSidebar"] .stRadio label:hover { color: #ffffff !important; }
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12) !important; }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] div { color: #c8d0e8; }
.app-header {
    background: linear-gradient(135deg, #ffffff 0%, #fff5f5 60%, #fffbf0 100%);
    border-bottom: 3px solid #c8102e;
    border-radius: 0 0 20px 20px;
    padding: 28px 40px 22px;
    margin: -1rem -1rem 2rem -1rem;
    position: relative; overflow: hidden;
    box-shadow: 0 4px 24px rgba(200,16,46,0.08);
}
.app-header::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px;
    background: linear-gradient(90deg, #8b0000 0%, #c8102e 40%, #d4af37 70%, #c8102e 100%);
}
.header-sinhala { font-family: 'Noto Sans Sinhala', sans-serif; font-size: 26px; font-weight: 800; color: #1e2340; line-height: 1.4; }
.header-english { font-family: 'Plus Jakarta Sans', sans-serif; font-size: 13px; font-weight: 600; color: #c8102e; letter-spacing: 0.08em; text-transform: uppercase; margin-top: 4px; }
.header-flag { font-size: 52px; margin-right: 20px; vertical-align: middle; }
.header-icon { font-size: 36px; margin-right: 14px; vertical-align: middle; }
.card {
    background: #ffffff; border: 1px solid #e2e6f0;
    border-left: 4px solid #c8102e; border-radius: 14px;
    padding: 20px; margin-bottom: 16px;
    transition: transform .2s, box-shadow .25s;
}
.card:hover { transform: translateY(-3px); box-shadow: 0 8px 32px rgba(200,16,46,0.10); border-left-color: #d4af37; }
.circ-table { width:100%; border-collapse:collapse; font-size:13px; }
.circ-table thead tr { background: #8b0000; color: #fff; }
.circ-table thead th { padding: 10px 12px; text-align: left; font-weight: 700; font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap; }
.circ-table tbody tr { border-bottom: 1px solid #e8eaf0; transition: background .15s; }
.circ-table tbody tr:hover { background: #fff5f5; }
.circ-table tbody td { padding: 9px 12px; vertical-align: middle; color: #374060; line-height: 1.4; }
.circ-table tbody td:first-child { font-weight: 700; color: #c8102e; white-space: nowrap; }
.circ-table tbody td.date-col { white-space: nowrap; color: #8a90a8; font-size: 12px; }
.circ-table tbody td.topic-col { max-width: 340px; }
.circ-table .dl-btn { display:inline-block; background: #c8102e; color: #fff !important; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 700; text-decoration: none; white-space: nowrap; }
.circ-table .dl-btn:hover { background: #a50d26; }
.circ-table-wrap { background: #fff; border: 1px solid #e2e6f0; border-radius: 12px; overflow: hidden; margin-bottom: 16px; }
.answer-box { background: #ffffff; border: 1px solid #e2e6f0; border-left: 4px solid #c8102e; border-radius: 4px 16px 16px 16px; padding: 22px 26px; margin: 14px 0; line-height: 1.85; font-size: 15px; color: #1e2340; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
.user-box { background: linear-gradient(135deg, #c8102e, #a50d26); border-radius: 16px 16px 4px 16px; padding: 16px 22px; margin: 14px 0 14px 15%; color: #fff; font-weight: 600; font-size: 15px; box-shadow: 0 4px 16px rgba(200,16,46,0.30); }
.b-en  { background: #e8f0ff; color: #1d4ed8; border: 1px solid #bfcfff; border-radius: 20px; padding: 4px 14px; font-size: 12px; font-weight: 700; }
.b-si  { background: #fff8e0; color: #92620a; border: 1px solid #f0d080; border-radius: 20px; padding: 4px 14px; font-size: 12px; font-weight: 700; font-family: 'Noto Sans Sinhala', sans-serif; }
.b-num { background: #fff0f2; color: #c8102e; border: 1px solid #f0c0c8; border-radius: 20px; padding: 4px 14px; font-size: 12px; font-weight: 700; }
.b-dl  { background: #fff4e8; color: #c2600a; border: 1px solid #f0c890; border-radius: 20px; padding: 4px 12px; font-size: 12px; font-weight: 600; }
.met { background: #ffffff; border: 1px solid #e2e6f0; border-top: 4px solid; border-radius: 16px; padding: 28px 18px 22px; text-align: center; transition: transform .2s, box-shadow .2s; box-shadow: 0 2px 12px rgba(0,0,0,0.05); }
.met:hover { transform: translateY(-4px); box-shadow: 0 12px 32px rgba(0,0,0,0.10); }
.met-val { font-size: 3rem; font-weight: 800; font-family: 'Lora', serif; line-height: 1.1; }
.met-lbl { color: #7a80a0; font-size: 12px; margin-top: 10px; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 600; }
.stButton > button { background: #ffffff !important; color: #374060 !important; border: 1.5px solid #d0d5e8 !important; border-radius: 10px !important; font-family: 'Plus Jakarta Sans', sans-serif !important; font-size: 14px !important; font-weight: 600 !important; transition: all .2s !important; padding: 8px 16px !important; }
.stButton > button:hover { border-color: #c8102e !important; color: #c8102e !important; background: #fff5f5 !important; }
button[data-testid="baseButton-primary"], .stFormSubmitButton > button { background: linear-gradient(135deg, #c8102e, #a50d26) !important; color: #fff !important; border: none !important; font-weight: 700 !important; }
.stTextInput > div > div > input { background: #ffffff !important; border: 2px solid #d8dcea !important; border-radius: 10px !important; color: #1e2340 !important; font-size: 15px !important; padding: 10px 14px !important; }
.stTextInput > div > div > input:focus { border-color: #c8102e !important; box-shadow: 0 0 0 3px rgba(200,16,46,0.10) !important; }
.stSelectbox > div > div { background: #ffffff !important; border: 2px solid #d8dcea !important; border-radius: 10px !important; color: #1e2340 !important; font-size: 15px !important; }
h1 { font-family: 'Lora', serif !important; color: #1e2340 !important; font-size: 2rem !important; }
h2 { color: #1e2340 !important; font-size: 1.4rem !important; font-weight: 700 !important; }
h3 { color: #374060 !important; font-size: 1.1rem !important; font-weight: 600 !important; }
.sidebar-brand-si { font-family: 'Noto Sans Sinhala', sans-serif; font-size: 14px; font-weight: 800; color: #ffffff; line-height: 1.7; text-align: center; }
.sidebar-brand-en { font-family: 'Plus Jakarta Sans', sans-serif; font-size: 10px; color: #f0a0b0; text-transform: uppercase; letter-spacing: 1.2px; font-weight: 700; text-align: center; margin-top: 5px; }
.status-pill { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.14); border-radius: 10px; padding: 8px 12px; margin-bottom: 7px; font-size: 13px; color: #c8d0e8; font-weight: 500; }
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #f0f2f8; }
::-webkit-scrollbar-thumb { background: #c8d0e8; border-radius: 10px; }
footer, #MainMenu { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def resolve_pdf_path(pdf_path_str: str) -> Path | None:
    if not pdf_path_str:
        return None
    base_dir = Path(__file__).resolve().parent
    fname = Path(pdf_path_str).name
    for p in [
        base_dir / pdf_path_str,
        base_dir / pdf_path_str.replace("\\", "/"),
        Path(pdf_path_str.replace("\\", "/")),
        base_dir / "downloads" / fname,
    ]:
        try:
            if p.exists():
                return p
        except Exception:
            pass
    return None


@st.cache_data(ttl=300)
def load_all_circulars() -> list[dict]:
    if not Path(DB_FILE).exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("""
        SELECT circular_number, issued_date, issued_by,
               topic, summary, key_instructions,
               applies_to, deadline, language, pdf_path
        FROM   circulars
        WHERE  summary IS NOT NULL
        ORDER BY
            CASE language WHEN 'S' THEN 0 ELSE 1 END,
            issued_date DESC
    """).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            ki = json.loads(r[5]) if r[5] else []
            if isinstance(ki, str):
                ki = [ki]
        except Exception:
            ki = []
        out.append({
            "circular_number" : (r[0] or "").strip(),
            "issued_date"     : r[1] or "",
            "issued_by"       : r[2] or "",
            "topic"           : r[3] or "",
            "summary"         : r[4] or "",
            "key_instructions": ki,
            "applies_to"      : r[6] or "",
            "deadline"        : r[7] or "",
            "language"        : r[8] or "S",
            "pdf_path"        : r[9] or "",
        })
    return out


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar(circulars):
    st.sidebar.markdown("""
    <div style='text-align:center;padding:24px 12px 16px'>
        <div style='font-size:48px;margin-bottom:10px'>🇱🇰</div>
        <div class='sidebar-brand-si'>ශ්‍රී ලංකා රජයේ<br>චක්‍රලේඛ නිරීක්ෂණ<br>පද්ධතිය</div>
        <div class='sidebar-brand-en'>Sri Lanka Government<br>Circulars Monitor</div>
        <div style='margin-top:12px;width:48px;height:2px;background:linear-gradient(90deg,#c8102e,#d4af37);border-radius:3px;margin-left:auto;margin-right:auto'></div>
    </div>""", unsafe_allow_html=True)

    st.sidebar.divider()
    page = st.sidebar.radio(
        "nav", ["🏠 Home", "🤖 AI Q&A", "📋 Browse", "📊 Dashboard", "⚙️ Setup"],
        label_visibility="collapsed",
    )
    st.sidebar.divider()

    api_key = os.environ.get("GROQ_API_KEY", "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")
    db_ok   = Path(DB_FILE).exists()
    vec_ok  = Path(CHROMA_DIR).exists()
    key_ok  = bool(api_key)
    n       = len(circulars)
    si      = sum(1 for c in circulars if c["language"] == "S")
    en      = sum(1 for c in circulars if c["language"] == "E")

    # ── Sinhala shown first in sidebar corpus counts ──
    st.sidebar.markdown(f"""
<div style='font-size:11px;color:rgba(255,255,255,0.45);text-transform:uppercase;letter-spacing:0.12em;font-weight:700;margin-bottom:8px'>System Status</div>
<div class='status-pill'>{"✅" if db_ok  else "❌"}&nbsp; Database &nbsp;<span style='color:#f87171;font-weight:700'>{n} circulars</span></div>
<div class='status-pill'>{"✅" if vec_ok else "⚠️"}&nbsp; Vector Store &nbsp;{"<span style='color:#f87171;font-size:11px'>Setup needed</span>" if not vec_ok else ""}</div>
<div class='status-pill' style='margin-bottom:14px'>{"✅" if key_ok else "❌"}&nbsp; Groq API Key</div>
<div style='font-size:11px;color:rgba(255,255,255,0.45);text-transform:uppercase;letter-spacing:0.12em;font-weight:700;margin-bottom:8px'>Corpus</div>
<div style='display:flex;gap:8px'>
    <div style='flex:1;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.14);border-radius:10px;padding:12px;text-align:center'>
        <div style='font-size:20px;font-weight:800;color:#fcd34d'>{si}</div>
        <div style='font-size:11px;color:rgba(255,255,255,0.5);margin-top:3px;font-weight:600;font-family:"Noto Sans Sinhala",sans-serif'>සිංහල</div>
    </div>
    <div style='flex:1;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.14);border-radius:10px;padding:12px;text-align:center'>
        <div style='font-size:20px;font-weight:800;color:#93c5fd'>{en}</div>
        <div style='font-size:11px;color:rgba(255,255,255,0.5);margin-top:3px;font-weight:600'>English</div>
    </div>
</div>
""", unsafe_allow_html=True)
    return page, api_key


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 0 — Home
# ══════════════════════════════════════════════════════════════════════════════

SUGGESTIONS = [
    "වැඩිහිටි නිලධාරීන්ට ලබා දෙන නිවාඩු මොනවාද?",
    "2025 වැටුප් සංශෝධන චක්‍රලේඛ මොනවාද?",
    "What are the salary revision circulars for 2025?",
    "Which circulars relate to leave for public officers?",
    "What is circular 10/2025 about?",
    "Show circulars with deadlines in 2026",
    "Tell me about festival advance payments",
    "Explain the annual transfer procedure",
]


def page_home(circulars: list, api_key: str):
    st.markdown("""
    <div class='app-header'>
        <span class='header-flag'>🇱🇰</span><span class='header-icon'>🏠</span>
        <div style='display:inline-block;vertical-align:middle'>
            <div class='header-sinhala'>ශ්‍රී ලංකා රජයේ චක්‍රලේඛ නිරීක්ෂණ පද්ධතිය</div>
            <div class='header-english'>Sri Lanka Government Circulars Monitor · Home</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if not circulars:
        st.error(f"Database not found: {DB_FILE}")
        return

    total = len(circulars)
    si    = sum(1 for c in circulars if c["language"] == "S")
    en    = sum(1 for c in circulars if c["language"] == "E")
    dls   = sum(1 for c in circulars if c["deadline"] and c["deadline"] not in ("null","None",""))
    yr25  = sum(1 for c in circulars if c["issued_date"].startswith("2025"))
    yr26  = sum(1 for c in circulars if c["issued_date"].startswith("2026"))

    # ── Sinhala first in metrics ──
    for col, (val, lbl, col_hex) in zip(
        st.columns(5),
        [(total, "සමස්ත / Total",     "#c8102e"),
         (si,    "සිංහල / Sinhala",   "#b45309"),
         (en,    "English",            "#1d4ed8"),
         (yr25,  "2025",               "#065f46"),
         (yr26,  "2026",               "#7c3aed")],
    ):
        col.markdown(f"""
        <div class='met' style='border-top-color:{col_hex}'>
            <div class='met-val' style='color:{col_hex}'>{val}</div>
            <div class='met-lbl'>{lbl}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("By Year")
        year_cnt = {}
        for c in circulars:
            y = (c["issued_date"] or "unknown")[:4]
            year_cnt[y] = year_cnt.get(y, 0) + 1
        for yr, cnt in sorted(year_cnt.items(), reverse=True)[:5]:
            pct = cnt * 100 // total
            st.markdown(f"""
            <div style='margin-bottom:12px'>
                <div style='display:flex;justify-content:space-between;font-size:14px;font-weight:500'>
                    <span style='color:#1e2340'>{yr}</span>
                    <span style='color:#c8102e;font-weight:700'>{cnt}</span>
                </div>
                <div style='background:#e8eaf0;border-radius:6px;height:10px;margin-top:5px'>
                    <div style='background:linear-gradient(90deg,#c8102e,#d4af37);width:{pct}%;height:10px;border-radius:6px'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    with col_r:
        st.subheader(" legedly Recent — සිංහල පළමු")
        # ── Sinhala first, then English ──
        recent_si = sorted([c for c in circulars if c["language"] == "S" and c["issued_date"]],
                           key=lambda x: x["issued_date"], reverse=True)[:5]
        recent_en = sorted([c for c in circulars if c["language"] == "E" and c["issued_date"]],
                           key=lambda x: x["issued_date"], reverse=True)[:5]
        for c in recent_si + recent_en:
            lb = '<span class="b-si">සිං</span>' if c["language"] == "S" else '<span class="b-en">EN</span>'
            t  = c["topic"][:45] + ("..." if len(c["topic"]) > 45 else "")
            st.markdown(f"""
            <div style='display:flex;justify-content:space-between;align-items:center;
                        padding:6px 0;border-bottom:1px solid #f0f2f8;font-size:13px'>
                <div><span style='color:#c8102e;font-weight:700'>{c["circular_number"]}</span>
                &nbsp;{lb}&nbsp;<span style='color:#374060'>{t}</span></div>
                <span style='color:#8a90a8;font-size:11px;white-space:nowrap'>{c["issued_date"]}</span>
            </div>""", unsafe_allow_html=True)

    dl_circulars = [c for c in circulars if c["deadline"] and c["deadline"] not in ("null","None","")]
    if dl_circulars:
        st.divider()
        st.subheader(f"⚠️ Upcoming Deadlines ({dls})")
        dcols = st.columns(min(3, len(dl_circulars)))
        for i, c in enumerate(dl_circulars[:6]):
            lb = '<span class="b-si">සිං</span>' if c["language"] == "S" else '<span class="b-en">EN</span>'
            t  = c["topic"][:55] + ("..." if len(c["topic"]) > 55 else "")
            dcols[i % 3].markdown(f"""
            <div class='card'>
                <span class='b-num'>{c["circular_number"]}</span>&nbsp;{lb}
                <div style='margin-top:8px;font-size:13px;color:#374060;font-weight:500'>{t}</div>
                <div style='margin-top:6px'><span class='b-dl'>⚠️ {c["deadline"]}</span></div>
            </div>""", unsafe_allow_html=True)

    st.divider()
    st.markdown("""
    <div style='background:linear-gradient(135deg,#1e2340,#2a3060);border-radius:16px;padding:22px 28px;margin-bottom:20px'>
        <div style='font-size:20px;font-weight:800;color:#ffffff;margin-bottom:4px'>🤖 AI Q&amp;A Agent</div>
        <div style='font-family:"Noto Sans Sinhala",sans-serif;font-size:15px;color:#e0e8ff;margin-bottom:6px'>
            ඔබට සිංහල භාෂාවෙන් AI ඒජන්තවරයෙකුගෙන් ප්‍රශ්න ඇසිය හැක
        </div>
        <div style='font-size:12px;color:#a0aac8;font-weight:600;text-transform:uppercase;letter-spacing:.08em'>
            Ask anything about Sri Lanka Government Circulars
        </div>
    </div>""", unsafe_allow_html=True)

    if not Path(CHROMA_DIR).exists():
        st.warning("Vector store not built yet. Go to ⚙️ Setup.")
        return
    if not api_key:
        st.warning("Groq API Key not found.")
        return
    try:
        from qa_chain import answer_question
    except ImportError as e:
        st.error(f"Missing package: {e}")
        return

    col_a, col_b = st.columns([3, 1])
    with col_a:
        # ── Default to Sinhala ──
        lang_sel = st.selectbox("Language", ["සිංහල පළමු / Sinhala First", "Both", "English only"], key="home_lang")
        lang_filter = {"සිංහල පළමු / Sinhala First": "S", "Both": None, "English only": "E"}[lang_sel]
    with col_b:
        k = st.slider("Sources", 3, 10, 5, key="home_k")

    if "home_history" not in st.session_state:
        st.session_state.home_history = []

    for turn_idx, turn in enumerate(st.session_state.home_history):
        st.markdown(f'<div class="user-box">🙋 {turn["question"]}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="answer-box">🤖&nbsp; {turn["answer"]}</div>', unsafe_allow_html=True)
        if turn.get("sources"):
            with st.expander(f"📎 {len(turn['sources'])} sources"):
                for src_idx, s in enumerate(turn["sources"]):
                    c1, c2, c3, c4, c5 = st.columns([1.4, 0.9, 0.7, 4, 1.3])
                    badge = '<span class="b-si">සිං</span>' if s["language"] == "S" else '<span class="b-en">EN</span>'
                    c1.markdown(f"<div style='padding:6px 0;font-weight:700;color:#c8102e'>{s['circular_number']}<br><small>{badge}</small></div>", unsafe_allow_html=True)
                    c2.markdown(f"<div style='padding:6px 0;color:#8a90a8;font-size:12px'>{s['issued_date'] or '—'}</div>", unsafe_allow_html=True)
                    c3.markdown(f"<div style='padding:6px 0;color:#059669;font-weight:700;font-size:12px'>{s['relevance_score']}%</div>", unsafe_allow_html=True)
                    c4.markdown(f"<div style='padding:6px 0;font-size:13px'>{s['topic'][:70]}...</div>", unsafe_allow_html=True)
                    pdf_full = resolve_pdf_path(s.get("pdf_path", ""))
                    if pdf_full:
                        safe_num = s["circular_number"].replace("/","_").replace(" ","_")
                        with open(pdf_full, "rb") as fh:
                            c5.download_button("📥 PDF", data=fh.read(), file_name=pdf_full.name,
                                               mime="application/pdf",
                                               key=f"home_dl_{turn_idx}_{src_idx}_{safe_num}",
                                               use_container_width=True)
                    else:
                        c5.markdown("<div style='padding:6px 0;color:#ccc'>—</div>", unsafe_allow_html=True)

    if not st.session_state.home_history:
        st.markdown("**💡 යෝජිත ප්‍රශ්න / Suggested questions:**")
        scols = st.columns(4)
        for i, sug in enumerate(SUGGESTIONS):
            if scols[i % 4].button(sug, key=f"home_s{i}", use_container_width=True):
                with st.spinner("🔍 Searching …  🤖 Asking Groq …"):
                    try:
                        res = answer_question(question=sug, api_key=api_key,
                                              lang_filter=lang_filter, n_results=k)
                        st.session_state.home_history.append(res)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

    with st.form("home_q_form", clear_on_submit=True):
        q = st.text_input("question", placeholder="ප්‍රශ්නය මෙහි ටයිප් කරන්න / Ask about any circular...", label_visibility="collapsed")
        submitted = st.form_submit_button("Ask →", type="primary")

    if submitted and q.strip():
        with st.spinner("🔍 Searching …  🤖 Asking Groq …"):
            try:
                res = answer_question(question=q, api_key=api_key,
                                      lang_filter=lang_filter, n_results=k)
                st.session_state.home_history.append(res)
                st.rerun()
            except Exception as e:
                import traceback
                st.error(f"Error: {e}")
                st.code(traceback.format_exc())

    if st.session_state.home_history:
        if st.button("🗑️ Clear Q&A", key="home_clear"):
            st.session_state.home_history = []
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — AI Q&A
# ══════════════════════════════════════════════════════════════════════════════

def page_qa(api_key: str):
    st.markdown("""
    <div class='app-header'>
        <span class='header-flag'>🇱🇰</span><span class='header-icon'>🤖</span>
        <div style='display:inline-block;vertical-align:middle'>
            <div class='header-sinhala'>AI ඒජන්තවරයාගෙන් අසන්න</div>
            <div style='font-family:"Noto Sans Sinhala",sans-serif;font-size:15px;color:#c8102e;margin-top:4px'>
                ඔබට සිංහල භාෂාවෙන් AI ඒජන්තවරයෙකුගෙන් ප්‍රශ්න ඇසිය හැක
            </div>
            <div class='header-english'>ChromaDB + LangChain + Groq llama-3.1-8b</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if not Path(CHROMA_DIR).exists():
        st.error("⚠️ Vector store not found. Go to **⚙️ Setup** and click **Build Vector Store**.")
        return
    if not api_key:
        st.warning("⚠️ Groq API Key not found.")
        return
    try:
        from qa_chain import answer_question
    except ImportError as e:
        st.error(f"Missing package: {e}")
        return

    col1, col2, col3 = st.columns([4, 1, 1])
    with col2:
        # ── Default to Sinhala ──
        lang_sel = st.selectbox("Language",
                                ["සිංහල", "Both", "English only"],
                                help="සිංහල = Sinhala circulars only")
        lang_map    = {"සිංහල": "S", "Both": None, "English only": "E"}
        lang_filter = lang_map[lang_sel]
    with col3:
        k = st.slider("Sources (k)", 3, 10, 5)

    if "history" not in st.session_state:
        st.session_state.history = []

    for turn_idx, turn in enumerate(st.session_state.history):
        st.markdown(f'<div class="user-box">🙋 {turn["question"]}</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="answer-box">🤖&nbsp; {turn["answer"]}</div>', unsafe_allow_html=True)
        if turn.get("sources"):
            with st.expander(f"📎 {len(turn['sources'])} sources", expanded=False):
                for src_idx, s in enumerate(turn["sources"]):
                    badge = '<span class="b-si">සිං</span>' if s["language"] == "S" else '<span class="b-en">EN</span>'
                    dl_badge = f'&nbsp;<span class="b-dl">⚠️ {s["deadline"]}</span>' if s.get("deadline") and s["deadline"] not in ("null","None","") else ""
                    bg = "#fff" if src_idx % 2 == 0 else "#fafafa"
                    c1, c2, c3, c4, c5 = st.columns([1.4, 0.9, 0.7, 4, 1.3])
                    with c1:
                        st.markdown(f"<div style='padding:8px 4px;background:{bg};font-weight:700;color:#c8102e;font-size:13px'>{s['circular_number']}<br><small>{badge}{dl_badge}</small></div>", unsafe_allow_html=True)
                    with c2:
                        st.markdown(f"<div style='padding:8px 4px;background:{bg};color:#8a90a8;font-size:12px'>{s['issued_date'] or '—'}</div>", unsafe_allow_html=True)
                    with c3:
                        st.markdown(f"<div style='padding:8px 4px;background:{bg};color:#059669;font-weight:700;font-size:12px'>{s['relevance_score']}%</div>", unsafe_allow_html=True)
                    with c4:
                        st.markdown(f"<div style='padding:8px 4px;background:{bg};font-size:13px;color:#374060'>{s['topic'][:75]}</div>", unsafe_allow_html=True)
                    with c5:
                        pdf_full = resolve_pdf_path(s.get("pdf_path", ""))
                        if pdf_full:
                            safe_num = s['circular_number'].replace("/","_").replace(" ","_")
                            with open(pdf_full, "rb") as f:
                                st.download_button("📥 PDF", data=f.read(), file_name=pdf_full.name,
                                                   mime="application/pdf",
                                                   key=f"dl_{turn_idx}_{src_idx}_{safe_num}",
                                                   use_container_width=True)
                        else:
                            st.markdown(f"<div style='padding:8px 4px;background:{bg};color:#ccc;font-size:12px'>—</div>", unsafe_allow_html=True)

    st.divider()

    if not st.session_state.history:
        st.markdown("**💡 යෝජිත ප්‍රශ්න / Suggested:**")
        cols = st.columns(4)
        for i, s in enumerate(SUGGESTIONS):
            if cols[i % 4].button(s, key=f"s{i}", use_container_width=True):
                with st.spinner("🔍 Searching …  🤖 Asking Groq …"):
                    try:
                        res = answer_question(question=s, api_key=api_key,
                                              lang_filter=lang_filter, n_results=k)
                        st.session_state.history.append(res)
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ {e}")

    with st.form("q_form", clear_on_submit=True):
        q = st.text_input("question",
                          placeholder="ප්‍රශ්නය සිංහලෙන් හෝ ඉංග්‍රීසියෙන් ලියන්න...",
                          label_visibility="collapsed")
        submitted = st.form_submit_button("Ask →", type="primary")

    if submitted and q.strip():
        with st.spinner("🔍 Searching …  🤖 Asking Groq …"):
            try:
                res = answer_question(question=q, api_key=api_key,
                                      lang_filter=lang_filter, n_results=k)
                st.session_state.history.append(res)
                st.rerun()
            except Exception as e:
                import traceback
                st.error(f"❌ {e}")
                st.code(traceback.format_exc())

    if st.session_state.history:
        if st.button("🗑️ Clear chat"):
            st.session_state.history = []
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — Browse  (Sinhala first by default)
# ══════════════════════════════════════════════════════════════════════════════

def page_browse(circulars: list):
    st.markdown("""
    <div class='app-header'>
        <span class='header-flag'>🇱🇰</span><span class='header-icon'>📋</span>
        <div style='display:inline-block;vertical-align:middle'>
            <div class='header-sinhala'>සියලු චක්‍රලේඛ · Browse</div>
            <div class='header-english'>Search &amp; Filter All Government Circulars</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if not circulars:
        st.error(f"Database not found: {DB_FILE}")
        return

    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        q = st.text_input("🔍 සොයන්න / Search",
                          placeholder="topic, circular number, keyword …")
    with c2:
        # ── Default to Sinhala ──
        lf = st.selectbox("Language", ["සිංහල", "All", "English"])
    with c3:
        dl_only = st.checkbox("Has deadline", False)

    filtered = circulars
    if q:
        ql = q.lower()
        filtered = [c for c in filtered if
                    ql in c["topic"].lower() or ql in c["circular_number"].lower() or
                    ql in c["summary"].lower() or ql in c["applies_to"].lower()]
    if lf == "සිංහල":
        filtered = [c for c in filtered if c["language"] == "S"]
    elif lf == "English":
        filtered = [c for c in filtered if c["language"] == "E"]
    if dl_only:
        filtered = [c for c in filtered if c["deadline"] and c["deadline"] not in ("null","None","")]

    st.caption(f"**{len(filtered)}** of **{len(circulars)}** circulars")
    st.divider()

    import base64 as _base64
    rows_html = ""
    for c in filtered:
        lang_badge = '<span class="b-si">සිං</span>' if c["language"] == "S" else '<span class="b-en">EN</span>'
        dl_badge   = f'<span class="b-dl">⚠️ {c["deadline"]}</span>' if c["deadline"] and c["deadline"] not in ("null","None","") else ""
        topic_disp   = c['topic'][:70] + ('...' if len(c['topic']) > 70 else '')
        summary_disp = c['summary'][:100] + ('...' if len(c['summary']) > 100 else '')
        pdf_cell = "&mdash;"
        pdf_path_obj = resolve_pdf_path(c.get("pdf_path", ""))
        if pdf_path_obj:
            with open(pdf_path_obj, "rb") as _f:
                _b64 = _base64.b64encode(_f.read()).decode()
            pdf_cell = f'<a class="dl-btn" href="data:application/pdf;base64,{_b64}" download="{pdf_path_obj.name}">📥 PDF</a>'
        rows_html += f"""<tr>
            <td>{c['circular_number']}</td>
            <td class='date-col'>{c['issued_date'] or '&mdash;'}</td>
            <td>{lang_badge}</td>
            <td class='topic-col'>{topic_disp}<br><span style='color:#8a90a8;font-size:11px'>{summary_disp}</span></td>
            <td class='date-col'>{dl_badge}</td>
            <td>{pdf_cell}</td>
        </tr>"""

    st.markdown(f"""
    <div class="circ-table-wrap">
    <table class="circ-table">
    <thead><tr>
        <th>Circular #</th><th>Date</th><th>Lang</th><th>Topic / Summary</th><th>Deadline</th><th>Download</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
    </table></div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — Dashboard  (Sinhala first)
# ══════════════════════════════════════════════════════════════════════════════

def page_dashboard(circulars: list):
    st.markdown("""
    <div class='app-header'>
        <span class='header-flag'>🇱🇰</span><span class='header-icon'>📊</span>
        <div style='display:inline-block;vertical-align:middle'>
            <div class='header-sinhala'>දත්ත පුවරුව · Dashboard</div>
            <div class='header-english'>Statistics, Analytics &amp; Deadline Tracker</div>
        </div>
    </div>""", unsafe_allow_html=True)

    if not circulars:
        st.error("No data.")
        return

    total = len(circulars)
    si    = sum(1 for c in circulars if c["language"] == "S")
    en    = sum(1 for c in circulars if c["language"] == "E")
    dls   = sum(1 for c in circulars if c["deadline"] and c["deadline"] not in ("null","None",""))
    yr25  = sum(1 for c in circulars if c["issued_date"].startswith("2025"))
    yr26  = sum(1 for c in circulars if c["issued_date"].startswith("2026"))

    # ── Sinhala first in metrics ──
    for col, (val, lbl, col_hex) in zip(
        st.columns(5),
        [(total, "සමස්ත / Total",     "#c8102e"),
         (si,    "සිංහල / Sinhala",   "#b45309"),
         (en,    "English",            "#1d4ed8"),
         (yr25,  "2025",               "#065f46"),
         (yr26,  "2026",               "#7c3aed")],
    ):
        col.markdown(f"""
        <div class="met" style="border-top-color:{col_hex}">
            <div class="met-val" style="color:{col_hex}">{val}</div>
            <div class="met-lbl">{lbl}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()
    col_l, col_r = st.columns(2)

    with col_l:
        st.subheader("📅 By Year")
        year_cnt = {}
        for c in circulars:
            y = (c["issued_date"] or "unknown")[:4]
            year_cnt[y] = year_cnt.get(y, 0) + 1
        for yr, cnt in sorted(year_cnt.items(), reverse=True):
            pct = cnt * 100 // total
            st.markdown(f"""
            <div style='margin-bottom:14px'>
                <div style='display:flex;justify-content:space-between;font-size:14px;font-weight:500'>
                    <span>{yr}</span><span style='color:#c8102e;font-weight:700'>{cnt}</span>
                </div>
                <div style='background:#e8eaf0;border-radius:6px;height:10px;margin-top:6px'>
                    <div style='background:linear-gradient(90deg,#c8102e,#d4af37);width:{pct}%;height:10px;border-radius:6px'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    with col_r:
        st.subheader("🏛️ By Ministry")
        min_cnt = {}
        for c in circulars:
            m = (c["issued_by"] or "Unknown")[:40]
            min_cnt[m] = min_cnt.get(m, 0) + 1
        for m, cnt in sorted(min_cnt.items(), key=lambda x: -x[1])[:6]:
            pct = cnt * 100 // total
            st.markdown(f"""
            <div style='margin-bottom:12px'>
                <div style='display:flex;justify-content:space-between;font-size:13px;font-weight:500'>
                    <span>{m}</span><span style='color:#1d4ed8;font-weight:700'>{cnt}</span>
                </div>
                <div style='background:#e8eaf0;border-radius:6px;height:8px;margin-top:5px'>
                    <div style='background:linear-gradient(90deg,#1d4ed8,#60a5fa);width:{pct}%;height:8px;border-radius:6px'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Sinhala circulars listed first ──
    st.subheader(f"⚠️ Circulars With Deadlines ({dls})")
    dl_circulars = [c for c in circulars if c["deadline"] and c["deadline"] not in ("null","None","")]
    dl_si = [c for c in dl_circulars if c["language"] == "S"]
    dl_en = [c for c in dl_circulars if c["language"] == "E"]
    for c in dl_si + dl_en:
        lb = '<span class="b-si">සිං</span>' if c["language"] == "S" else '<span class="b-en">EN</span>'
        st.markdown(f"""
        <div class="card" style='display:flex;justify-content:space-between;align-items:center'>
            <div>
                <span class="b-num">{c['circular_number']}</span>&nbsp;{lb}&nbsp;
                <span style='color:#374060;font-size:14px;font-weight:500'>{c['topic'][:65]}{"…" if len(c["topic"])>65 else ""}</span>
            </div>
            <span class="b-dl" style='white-space:nowrap'>⚠️ {c['deadline']}</span>
        </div>""", unsafe_allow_html=True)

    st.divider()
    st.subheader("🕐 Most Recent — සිංහල පළමු")
    import pandas as pd
    # ── Sinhala first, then English ──
    recent_si = sorted([c for c in circulars if c["language"] == "S" and c["issued_date"]],
                       key=lambda x: x["issued_date"], reverse=True)[:5]
    recent_en = sorted([c for c in circulars if c["language"] == "E" and c["issued_date"]],
                       key=lambda x: x["issued_date"], reverse=True)[:5]
    df = pd.DataFrame([{
        "Number"  : c["circular_number"],
        "Date"    : c["issued_date"],
        "Lang"    : "සිංහල" if c["language"] == "S" else "English",
        "Topic"   : c["topic"][:65] + ("…" if len(c["topic"]) > 65 else ""),
        "Deadline": c["deadline"] or "—",
    } for c in recent_si + recent_en])
    st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — Setup
# ══════════════════════════════════════════════════════════════════════════════

def page_setup():
    st.markdown("""
    <div class='app-header'>
        <span class='header-flag'>⚙️</span>
        <div style='display:inline-block;vertical-align:middle'>
            <div class='header-sinhala'>පද්ධති සැකසුම</div>
            <div class='header-english'>Setup · Configuration &amp; Installation</div>
        </div>
    </div>""", unsafe_allow_html=True)

    with st.expander("**Step 1 — Install packages**", expanded=True):
        st.code("pip install -r requirements.txt", language="bash")

    with st.expander("**Step 2 — Build vector store**", expanded=True):
        st.code("python build_vectorstore.py", language="bash")
        status_col, btn_col = st.columns([2, 1])
        with status_col:
            if Path(CHROMA_DIR).exists():
                st.success("✅ Vector store found — ready!")
            else:
                st.warning("⚠️ Vector store not built yet")
        with btn_col:
            if st.button("🔨 Build Now", type="primary", use_container_width=True):
                if not Path(DB_FILE).exists():
                    st.error(f"Cannot find {DB_FILE}")
                else:
                    try:
                        from build_vectorstore import build_vectorstore
                        import io, sys
                        old_stdout = sys.stdout
                        sys.stdout = buf = io.StringIO()
                        build_vectorstore()
                        sys.stdout = old_stdout
                        st.success("✅ Vector store built!")
                        st.balloons()
                    except Exception as e:
                        sys.stdout = old_stdout
                        st.error(f"Error: {e}")

    with st.expander("**Step 3 — Start app**"):
        st.code("streamlit run app.py", language="bash")
        st.success("✅ Already running!")

    st.divider()
    st.subheader("📁 File Structure")
    st.code("""
sl-circulars-monitor/
├── app.py                  ← Streamlit UI
├── qa_chain.py             ← LangChain RAG chain
├── build_vectorstore.py    ← ChromaDB builder
├── run_pipeline.py         ← Daily pipeline
├── new_detector.py         ← Week 8: new circular detector
├── reprocess_sinhala.py    ← Sinhala fix tool
├── requirements.txt
├── circulars.db
└── chroma_db/
    """)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    circulars     = load_all_circulars()
    page, api_key = render_sidebar(circulars)

    if page == "🏠 Home":
        page_home(circulars, api_key)
    elif page == "🤖 AI Q&A":
        page_qa(api_key)
    elif page == "📋 Browse":
        page_browse(circulars)
    elif page == "📊 Dashboard":
        page_dashboard(circulars)
    elif page == "⚙️ Setup":
        page_setup()


if __name__ == "__main__":
    main()
