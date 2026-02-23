"""
app.py  —  Week 7: Streamlit AI Agent
=======================================
Sri Lanka Government Circulars — AI Q&A + Browse + Dashboard

Launch:
    streamlit run app.py

Four pages:
    🤖 AI Q&A        — RAG-powered chat (ChromaDB + LangChain + Groq)
    📋 Browse         — Search & filter all 87 circulars
    📊 Dashboard      — Stats, charts, deadline tracker
    ⚙️  Setup         — One-click vector store builder + instructions
"""

import os
import sqlite3
import json
from pathlib import Path

import streamlit as st

# ── Must be first Streamlit call ─────────────────────────────────────────────
st.set_page_config(
    page_title="SL Circulars AI Agent",
    page_icon="🇱🇰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Paths (match run_pipeline.py) ─────────────────────────────────────────────
DB_FILE    = "circulars.db"
CHROMA_DIR = "./chroma_db"


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Dark base */
.stApp, [data-testid="stAppViewContainer"] { background:#07111f; color:#dde6f0; }
section[data-testid="stSidebar"] { background:#0b1a2e; border-right:1px solid #1a3050; }

/* Cards */
.card {
    background:#0e2038; border:1px solid #1a3050;
    border-radius:12px; padding:16px; margin-bottom:12px;
    transition:border-color .2s;
}
.card:hover { border-color:#10b981; }

/* AI answer bubble */
.answer-box {
    background:#0e2038; border:1px solid #10b981;
    border-radius:4px 14px 14px 14px;
    padding:18px 20px; margin:10px 0; line-height:1.75;
}

/* User bubble */
.user-box {
    background:linear-gradient(135deg,#10b981,#059669);
    border-radius:14px 14px 4px 14px;
    padding:12px 18px; margin:10px 0 10px 20%;
    color:#fff;
}

/* Badges */
.b-en  { background:#3b82f625;color:#60a5fa;border:1px solid #3b82f645;
         border-radius:20px;padding:2px 10px;font-size:11px;font-weight:700; }
.b-si  { background:#f59e0b25;color:#fbbf24;border:1px solid #f59e0b45;
         border-radius:20px;padding:2px 10px;font-size:11px;font-weight:700; }
.b-num { background:#10b98120;color:#10b981;border:1px solid #10b98140;
         border-radius:20px;padding:2px 10px;font-size:11px;font-weight:700; }
.b-dl  { background:#ef444425;color:#f87171;border:1px solid #ef444445;
         border-radius:20px;padding:2px 8px;font-size:11px; }

/* Relevance bar */
.rb-out { background:#1a3050;border-radius:4px;height:5px;margin-top:5px; }
.rb-in  { background:#10b981;border-radius:4px;height:5px; }

/* Metric */
.met { background:#0e2038;border:1px solid #1a3050;border-radius:12px;
       padding:20px;text-align:center; }
.met-val { font-size:2.4rem;font-weight:800; }
.met-lbl { color:#6b7280;font-size:13px;margin-top:4px; }

/* Hide Streamlit chrome */
footer, #MainMenu { visibility:hidden; }
</style>
""", unsafe_allow_html=True)


# ── DB helpers ────────────────────────────────────────────────────────────────

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
        ORDER  BY issued_date DESC
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
            "circular_number": (r[0] or "").strip(),
            "issued_date"    : r[1] or "",
            "issued_by"      : r[2] or "",
            "topic"          : r[3] or "",
            "summary"        : r[4] or "",
            "key_instructions": ki,
            "applies_to"     : r[6] or "",
            "deadline"       : r[7] or "",
            "language"       : r[8] or "E",
            "pdf_path"       : r[9] or "",
        })
    return out


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar(circulars):
    st.sidebar.markdown("""
    <div style='text-align:center;padding:18px 0 10px'>
        <div style='font-size:44px'>🇱🇰</div>
        <div style='font-weight:800;font-size:16px;color:#f0fdf4;margin-top:6px'>
            SL Circulars AI Agent
        </div>
        <div style='color:#6b7280;font-size:11px'>රාජ්‍ය චක්‍රලේඛ · 2025–2026</div>
    </div>""", unsafe_allow_html=True)

    st.sidebar.divider()
    page = st.sidebar.radio(
        "nav", ["🤖 AI Q&A", "📋 Browse", "📊 Dashboard", "⚙️ Setup"],
        label_visibility="collapsed",
    )

    st.sidebar.divider()
    # API key — check env first, then sidebar input
    env_key = os.environ.get("GROQ_API_KEY", "")
    api_key = st.sidebar.text_input(
        "🔑 Groq API Key",
        value=env_key,
        type="password",
        placeholder="gsk_...",
        help="Free key at console.groq.com",
    )
    if api_key:
        os.environ["GROQ_API_KEY"] = api_key

    st.sidebar.divider()
    # Status indicators
    db_ok  = Path(DB_FILE).exists()
    vec_ok = Path(CHROMA_DIR).exists()
    key_ok = bool(api_key)
    n      = len(circulars)
    en     = sum(1 for c in circulars if c["language"] == "E")
    si     = sum(1 for c in circulars if c["language"] == "S")

    st.sidebar.markdown(f"""
**Status**
{"✅" if db_ok  else "❌"} Database ({n} circulars)
{"✅" if vec_ok else "⚠️"} Vector Store {"" if vec_ok else "— run Setup"}
{"✅" if key_ok else "❌"} Groq API Key

**Corpus**
🇬🇧 English: **{en}**
🇱🇰 Sinhala: **{si}**
""")
    return page, api_key


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 1 — AI Q&A
# ══════════════════════════════════════════════════════════════════════════════

SUGGESTIONS = [
    "What are the salary revision circulars for 2025?",
    "Which circulars relate to leave for public officers?",
    "What is circular 10/2025 about?",
    "Show circulars with deadlines in 2026",
    "Tell me about festival advance payments",
    "What are the pension revision rules?",
    "Which circulars apply to all public officers?",
    "Explain the annual transfer procedure",
]


def page_qa(api_key: str):
    st.title("🤖 AI Q&A Agent")
    st.caption("Semantic search over 87 circulars · ChromaDB + LangChain + Groq llama-3.1-8b")

    # Guard rails
    if not Path(CHROMA_DIR).exists():
        st.error("⚠️ Vector store not found. Go to **⚙️ Setup** and click **Build Vector Store**.")
        return
    if not api_key:
        st.warning("⚠️ Enter your Groq API Key in the sidebar.")
        return

    try:
        from qa_chain import answer_question
    except ImportError as e:
        st.error(f"Missing package: {e}\nRun: pip install -r requirements.txt")
        return

    # Settings
    col1, col2, col3 = st.columns([4, 1, 1])
    with col2:
        lang_sel = st.selectbox("Filter language",
                                ["Both", "English only", "Sinhala only"])
        lang_map = {"Both": None, "English only": "E", "Sinhala only": "S"}
        lang_filter = lang_map[lang_sel]
    with col3:
        k = st.slider("Sources (k)", 3, 10, 5)

    # Chat history
    if "history" not in st.session_state:
        st.session_state.history = []

    # Render past turns
    for turn in st.session_state.history:
        st.markdown(f'<div class="user-box">🙋 {turn["question"]}</div>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="answer-box">🤖&nbsp; {turn["answer"]}</div>',
                    unsafe_allow_html=True)

        if turn.get("sources"):
            with st.expander(f"📎 {len(turn['sources'])} sources", expanded=False):
                for s in turn["sources"]:
                    badge = '<span class="b-en">EN</span>' if s["language"] == "E" \
                            else '<span class="b-si">සිං</span>'
                    dl    = (f'&nbsp;<span class="b-dl">⚠️ {s["deadline"]}</span>'
                             if s["deadline"] else "")
                    st.markdown(f"""
                    <div class="card">
                        <span class="b-num">{s['circular_number']}</span>
                        &nbsp;{badge}{dl}
                        &nbsp;·&nbsp;{s['issued_date']}
                        &nbsp;·&nbsp;
                        <span style='color:#10b981;font-weight:700'>
                            {s['relevance_score']}% match
                        </span>
                        <div class="rb-out">
                            <div class="rb-in" style="width:{s['relevance_score']}%"></div>
                        </div>
                        <div style='color:#cbd5e1;font-size:14px;margin-top:8px'>
                            {s['topic']}
                        </div>
                        <div style='color:#6b7280;font-size:12px;margin-top:4px'>
                            {s['summary'][:180]}…
                        </div>
                    </div>""", unsafe_allow_html=True)

    st.divider()

    # Suggested questions (only on first load)
    if not st.session_state.history:
        st.markdown("**💡 Suggested questions:**")
        cols = st.columns(4)
        for i, s in enumerate(SUGGESTIONS):
            if cols[i % 4].button(s, key=f"s{i}", use_container_width=True):
                st.session_state["_pending"] = s
                st.rerun()

    # Input form
    with st.form("q_form", clear_on_submit=True):
        q = st.text_input(
            "question",
            value=st.session_state.pop("_pending", ""),
            placeholder="e.g. What are the rules for disciplinary inquiry payments?",
            label_visibility="collapsed",
        )
        submitted = st.form_submit_button("Ask →", type="primary")

    if submitted and q.strip():
        with st.spinner("🔍 Searching vector store …  🤖 Asking Groq …"):
            try:
                res = answer_question(
                    question=q, api_key=api_key,
                    lang_filter=lang_filter, n_results=k,
                )
                st.session_state.history.append(res)
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    if st.session_state.history:
        if st.button("🗑️ Clear chat"):
            st.session_state.history = []
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2 — Browse
# ══════════════════════════════════════════════════════════════════════════════

def page_browse(circulars: list):
    st.title("📋 Browse All Circulars")

    if not circulars:
        st.error(f"Database not found: {DB_FILE}")
        return

    # Filters
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        q = st.text_input("🔍 Search",
                          placeholder="topic, circular number, keyword …")
    with c2:
        lf = st.selectbox("Language", ["All", "English", "Sinhala"])
    with c3:
        dl_only = st.checkbox("Has deadline", False)

    filtered = circulars
    if q:
        ql = q.lower()
        filtered = [c for c in filtered if
                    ql in c["topic"].lower() or
                    ql in c["circular_number"].lower() or
                    ql in c["summary"].lower() or
                    ql in c["applies_to"].lower()]
    if lf == "English":
        filtered = [c for c in filtered if c["language"] == "E"]
    elif lf == "Sinhala":
        filtered = [c for c in filtered if c["language"] == "S"]
    if dl_only:
        filtered = [c for c in filtered
                    if c["deadline"] and c["deadline"] not in ("null", "None", "")]

    st.caption(f"**{len(filtered)}** of **{len(circulars)}** circulars")
    st.divider()

    ca, cb = st.columns(2)
    for i, c in enumerate(filtered):
        col = ca if i % 2 == 0 else cb
        lbadge = '<span class="b-en">English</span>' if c["language"] == "E" \
                 else '<span class="b-si">සිංහල</span>'
        dlbadge = (f'&nbsp;<span class="b-dl">⚠️ {c["deadline"]}</span>'
                   if c["deadline"] and c["deadline"] not in ("null", "None", "") else "")

        col.markdown(f"""
        <div class="card">
            <div style='display:flex;justify-content:space-between;margin-bottom:8px'>
                <span class="b-num">{c['circular_number']}</span>
                <span>{lbadge}{dlbadge}</span>
            </div>
            <div style='font-weight:600;color:#f0fdf4;font-size:14px;margin-bottom:6px'>
                {c['topic'][:85]}{'…' if len(c['topic'])>85 else ''}
            </div>
            <div style='color:#94a3b8;font-size:13px;line-height:1.55'>
                {c['summary'][:160]}{'…' if len(c['summary'])>160 else ''}
            </div>
            <div style='color:#6b7280;font-size:11px;margin-top:8px'>
                📅 {c['issued_date'] or '—'}
                &nbsp;·&nbsp; 🏛️ {(c['issued_by'] or '')[:45]}
            </div>
        </div>
        """, unsafe_allow_html=True)

        with col.expander(f"Full details — {c['circular_number']}"):
            if c["key_instructions"]:
                st.markdown("**Key Instructions:**")
                for inst in c["key_instructions"]:
                    st.markdown(f"› {inst}")
            if c["applies_to"]:
                st.markdown(f"**Applies To:** {c['applies_to']}")
            if c["deadline"] and c["deadline"] not in ("null", "None", ""):
                st.markdown(f"**⚠️ Deadline:** {c['deadline']}")
            st.markdown(f"**Issued By:** {c['issued_by']}")

            # ── PDF Download ──────────────────────────────────────
            pdf = c.get("pdf_path", "")
            if pdf:
                # Resolve relative to app.py location
                base_dir = Path(__file__).parent
                pdf_path = base_dir / Path(pdf.replace("\\", "/"))
                if pdf_path.exists():
                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            label="📥 Download PDF",
                            data=f.read(),
                            file_name=pdf_path.name,
                            mime="application/pdf",
                            key=f"dl_{c['circular_number']}_{c['language']}",
                            use_container_width=True,
                        )
                else:
                    st.caption("📄 PDF not available")


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 3 — Dashboard
# ══════════════════════════════════════════════════════════════════════════════

def page_dashboard(circulars: list):
    st.title("📊 Dashboard")
    if not circulars:
        st.error("No data.")
        return

    total = len(circulars)
    en    = sum(1 for c in circulars if c["language"] == "E")
    si    = sum(1 for c in circulars if c["language"] == "S")
    dls   = sum(1 for c in circulars
                if c["deadline"] and c["deadline"] not in ("null", "None", ""))
    yr25  = sum(1 for c in circulars if c["issued_date"].startswith("2025"))
    yr26  = sum(1 for c in circulars if c["issued_date"].startswith("2026"))

    # Metric row
    for col, (val, lbl, col_hex) in zip(
        st.columns(5),
        [(total, "Total Circulars", "#10b981"),
         (en,    "English",         "#3b82f6"),
         (si,    "Sinhala (සිංහල)", "#f59e0b"),
         (yr25,  "Year 2025",       "#8b5cf6"),
         (yr26,  "Year 2026",       "#ec4899")],
    ):
        col.markdown(f"""
        <div class="met">
            <div class="met-val" style="color:{col_hex}">{val}</div>
            <div class="met-lbl">{lbl}</div>
        </div>""", unsafe_allow_html=True)

    st.divider()
    col_l, col_r = st.columns(2)

    # — By year breakdown
    with col_l:
        st.subheader("📅 By Year")
        year_cnt = {}
        for c in circulars:
            y = (c["issued_date"] or "unknown")[:4]
            year_cnt[y] = year_cnt.get(y, 0) + 1
        for yr, cnt in sorted(year_cnt.items(), reverse=True):
            pct = cnt * 100 // total
            st.markdown(f"""
            <div style='margin-bottom:10px'>
                <div style='display:flex;justify-content:space-between;font-size:13px'>
                    <span style='color:#e2e8f0'>{yr}</span>
                    <span style='color:#10b981;font-weight:700'>{cnt}</span>
                </div>
                <div style='background:#1a3050;border-radius:4px;height:8px;margin-top:4px'>
                    <div style='background:#10b981;width:{pct}%;height:8px;border-radius:4px'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    # — By ministry
    with col_r:
        st.subheader("🏛️ By Ministry")
        min_cnt = {}
        for c in circulars:
            m = (c["issued_by"] or "Unknown")[:40]
            min_cnt[m] = min_cnt.get(m, 0) + 1
        for m, cnt in sorted(min_cnt.items(), key=lambda x: -x[1])[:6]:
            pct = cnt * 100 // total
            st.markdown(f"""
            <div style='margin-bottom:10px'>
                <div style='display:flex;justify-content:space-between;font-size:12px'>
                    <span style='color:#e2e8f0'>{m}</span>
                    <span style='color:#3b82f6;font-weight:700'>{cnt}</span>
                </div>
                <div style='background:#1a3050;border-radius:4px;height:6px;margin-top:3px'>
                    <div style='background:#3b82f6;width:{pct}%;height:6px;border-radius:4px'></div>
                </div>
            </div>""", unsafe_allow_html=True)

    st.divider()

    # — Deadlines
    st.subheader(f"⚠️ Circulars With Deadlines  ({dls})")
    dl_circulars = [c for c in circulars
                    if c["deadline"] and c["deadline"] not in ("null", "None", "")]
    if dl_circulars:
        for c in dl_circulars:
            lb = '<span class="b-en">EN</span>' if c["language"] == "E" \
                 else '<span class="b-si">සිං</span>'
            st.markdown(f"""
            <div class="card" style='display:flex;justify-content:space-between;align-items:center'>
                <div>
                    <span class="b-num">{c['circular_number']}</span>
                    &nbsp;{lb}&nbsp;
                    <span style='color:#cbd5e1;font-size:13px'>
                        {c['topic'][:65]}{'…' if len(c['topic'])>65 else ''}
                    </span>
                </div>
                <span class="b-dl" style='white-space:nowrap'>⚠️ {c['deadline']}</span>
            </div>""", unsafe_allow_html=True)

    st.divider()

    # — Recent circulars table
    st.subheader("🕐 10 Most Recent")
    import pandas as pd
    recent = sorted(
        [c for c in circulars if c["issued_date"]],
        key=lambda x: x["issued_date"], reverse=True
    )[:10]
    df = pd.DataFrame([{
        "Number"  : c["circular_number"],
        "Date"    : c["issued_date"],
        "Lang"    : "English" if c["language"] == "E" else "Sinhala",
        "Topic"   : c["topic"][:65] + ("…" if len(c["topic"]) > 65 else ""),
        "Deadline": c["deadline"] or "—",
    } for c in recent])
    st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 4 — Setup
# ══════════════════════════════════════════════════════════════════════════════

def page_setup():
    st.title("⚙️ Setup")

    with st.expander("**Step 1 — Install packages**", expanded=True):
        st.code("pip install -r requirements.txt", language="bash")

    with st.expander("**Step 2 — Get Groq API key (free)**"):
        st.markdown("""
1. Go to **[console.groq.com](https://console.groq.com)**
2. Sign up → **API Keys** → **Create API Key**
3. Paste it in the sidebar **🔑 Groq API Key** box
        """)

    with st.expander("**Step 3 — Build vector store**", expanded=True):
        st.markdown("Make sure `circulars.db` is in the same folder, then:")
        st.code("python build_vectorstore.py", language="bash")
        st.markdown("""
- Loads all 87 circulars (55 EN + 32 SI) from your SQLite DB
- Embeds with `all-MiniLM-L6-v2` (free, CPU, ~90 MB download once)
- Saves ChromaDB to `./chroma_db/`
- Takes ~60 sec on first run
        """)

        status_col, btn_col = st.columns([2, 1])
        with status_col:
            if Path(CHROMA_DIR).exists():
                st.success("✅ Vector store found — you're ready!")
            else:
                st.warning("⚠️ Vector store not built yet")
        with btn_col:
            if st.button("🔨 Build Now", type="primary", use_container_width=True):
                if not Path(DB_FILE).exists():
                    st.error(f"Cannot find {DB_FILE} in this folder.")
                else:
                    progress = st.progress(0, text="Starting …")
                    status   = st.empty()
                    try:
                        from build_vectorstore import build_vectorstore
                        import io, sys
                        # Capture stdout so we can update status
                        old_stdout = sys.stdout
                        sys.stdout = buf = io.StringIO()
                        build_vectorstore()
                        sys.stdout = old_stdout
                        progress.progress(100, text="Done!")
                        st.success("✅ Vector store built successfully!")
                        st.balloons()
                        st.info(buf.getvalue())
                    except Exception as e:
                        sys.stdout = old_stdout
                        st.error(f"Error: {e}")

    with st.expander("**Step 4 — Start app**"):
        st.code("streamlit run app.py", language="bash")
        st.success("✅ You're already here — the app is running!")

    st.divider()
    st.subheader("📁 File Structure")
    st.code("""
week7/
├── app.py                  ← Streamlit UI  (this file)
├── qa_chain.py             ← LangChain RAG chain
├── build_vectorstore.py    ← ChromaDB builder  (run once)
├── requirements.txt        ← Python packages
├── circulars.db            ← your SQLite DB from weeks 1-6
└── chroma_db/              ← auto-created by build_vectorstore.py
    """)

    st.divider()
    st.subheader("🏗️ Architecture")
    st.markdown("""
```
User Question
      │
      ▼
┌──────────────────────────────────────────────┐
│  ChromaDB  (87 circulars as 384-dim vectors) │
│  Model: all-MiniLM-L6-v2  (local, free)      │
│  Returns TOP-5 most similar circulars         │
└──────────────────────────────────────────────┘
      │  5 circular contexts
      ▼
┌──────────────────────────────────────────────┐
│  LangChain ChatPromptTemplate                │
│  system: "You are a circulars expert…"       │
│  human:  "User question + context"           │
└──────────────────────────────────────────────┘
      │  prompt
      ▼
┌──────────────────────────────────────────────┐
│  Groq  llama-3.1-8b-instant                  │
│  Free tier: 6000 tokens/min                  │
└──────────────────────────────────────────────┘
      │  answer
      ▼
Streamlit UI — answer + source cards + relevance %
```

**Why this stack?**
| Component | Choice | Reason |
|-----------|--------|--------|
| Embeddings | `all-MiniLM-L6-v2` | Free, CPU, 384-dim, accurate |
| Vector DB | ChromaDB | On-disk, no server, Python-native |
| LLM | Groq llama-3.1-8b | Fastest free API (6k TPM) |
| Framework | LangChain | Prompt management + chain chaining |
| UI | Streamlit | Zero HTML, rapid dev |
    """)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    circulars     = load_all_circulars()
    page, api_key = render_sidebar(circulars)

    if page == "🤖 AI Q&A":
        page_qa(api_key)
    elif page == "📋 Browse":
        page_browse(circulars)
    elif page == "📊 Dashboard":
        page_dashboard(circulars)
    elif page == "⚙️ Setup":
        page_setup()


if __name__ == "__main__":
    main()
