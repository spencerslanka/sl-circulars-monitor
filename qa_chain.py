"""
qa_chain.py  —  Week 7, Step 2
================================
LangChain RAG pipeline:
  1. Embed the user's question
  2. Search ChromaDB for the TOP-K most similar circulars
  3. Build a prompt with the retrieved context
  4. Send to Groq (llama-3.1-8b-instant, free tier)
  5. Return structured answer + source circulars

Used by app.py — can also be tested from the CLI:
    export GROQ_API_KEY=gsk_...
    python qa_chain.py
"""

import json
import os
import sqlite3
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# ── Config ────────────────────────────────────────────────────────────────────
CHROMA_DIR  = "./chroma_db"
COLLECTION  = "circulars"
EMBED_MODEL = "all-MiniLM-L6-v2"
GROQ_MODEL  = "llama-3.1-8b-instant"   # fastest on Groq free tier
DEFAULT_K   = 5                          # circulars to retrieve per query
DB_FILE     = "./circulars.db"


def _fetch_pdf_paths(circular_numbers: list[str]) -> dict[str, str]:
    """Look up pdf_path for a list of circular numbers from SQLite."""
    if not circular_numbers or not os.path.exists(DB_FILE):
        return {}
    try:
        conn = sqlite3.connect(DB_FILE)
        placeholders = ",".join("?" * len(circular_numbers))
        rows = conn.execute(
            f"SELECT circular_number, pdf_path FROM circulars WHERE circular_number IN ({placeholders})",
            circular_numbers
        ).fetchall()
        conn.close()
        return {r[0]: r[1] or "" for r in rows}
    except Exception:
        return {}

# ── Singletons (loaded once per Streamlit session) ───────────────────────────
_collection = None
_llm_cache  = {}


def get_collection():
    global _collection
    if _collection is None:
        embed_fn    = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=EMBED_MODEL
        )
        client      = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_collection(
            name=COLLECTION, embedding_function=embed_fn
        )
    return _collection


def get_llm(api_key: str) -> ChatGroq:
    if api_key not in _llm_cache:
        _llm_cache[api_key] = ChatGroq(
            groq_api_key=api_key,
            model_name=GROQ_MODEL,
            temperature=0.1,    # low = factual, consistent
            max_tokens=900,
        )
    return _llm_cache[api_key]


# ── Retrieval ─────────────────────────────────────────────────────────────────

def retrieve(question: str,
             lang_filter: Optional[str] = None,
             n: int = DEFAULT_K) -> list[dict]:
    """
    Semantic search in ChromaDB.
    lang_filter: 'E' = English only, 'S' = Sinhala only, None = both
    Returns list of hit dicts sorted by relevance.
    """
    col = get_collection()
    where = {"language": lang_filter} if lang_filter in ("E", "S") else None

    results = col.query(
        query_texts=[question],
        n_results=n,
        where=where,
    )

    hits = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        # Parse stored key_instructions back to list
        try:
            ki = json.loads(meta.get("key_instructions_json", "[]"))
        except Exception:
            ki = []

        hits.append({
            "document"        : doc,
            "circular_number" : meta.get("circular_number", ""),
            "topic"           : meta.get("topic", ""),
            "issued_date"     : meta.get("issued_date", ""),
            "issued_by"       : meta.get("issued_by", ""),
            "applies_to"      : meta.get("applies_to", ""),
            "deadline"        : meta.get("deadline", ""),
            "language"        : meta.get("language", "E"),
            "summary"         : meta.get("summary", ""),
            "key_instructions": ki,
            "relevance_score" : round((1 - dist) * 100, 1),   # cosine → %
            "pdf_path"        : "",   # filled in below
        })

    # ── Enrich hits with pdf_path from SQLite ────────────────────────────────
    pdf_map = _fetch_pdf_paths([h["circular_number"] for h in hits])
    for h in hits:
        h["pdf_path"] = pdf_map.get(h["circular_number"], "")

    return hits


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """You are an expert assistant for Sri Lanka Government Public Administration Circulars (රාජ්‍ය චක්‍රලේඛ).

Answer questions using ONLY the circular documents provided in the context.
Always cite circular numbers and dates. Be specific and accurate.
If the answer is not found in the context, say so clearly — never fabricate.

CONTEXT — Retrieved Circulars (ordered by relevance):
{context}"""

_HUMAN = """{question}

Give a clear, structured answer. Include circular numbers, dates, and key details."""


def _build_context(hits: list[dict]) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        lang = "English" if h["language"] == "E" else "Sinhala (සිංහල)"
        ki   = "\n".join(f"    • {x}" for x in h["key_instructions"]) \
               if h["key_instructions"] else "    (not extracted)"
        parts.append(
            f"[{i}] Circular {h['circular_number']}  |  {h['issued_date']}  |  {lang}  "
            f"|  Relevance: {h['relevance_score']}%\n"
            f"Topic: {h['topic']}\n"
            f"Summary: {h['summary']}\n"
            f"Key Instructions:\n{ki}\n"
            f"Applies To: {h['applies_to'] or 'not specified'}\n"
            f"Deadline: {h['deadline'] or 'none'}\n"
            f"Issued By: {h['issued_by']}"
        )
    return "\n\n" + "\n\n---\n\n".join(parts)


# ── Main Q&A entry point ──────────────────────────────────────────────────────

def answer_question(
    question   : str,
    api_key    : str,
    lang_filter: Optional[str] = None,
    n_results  : int = DEFAULT_K,
) -> dict:
    """
    Full RAG pipeline. Returns:
    {
        "answer"  : str,
        "sources" : list[dict],   # retrieved circulars with scores
        "question": str,
    }
    """
    if not api_key:
        raise ValueError("GROQ_API_KEY is required")

    # Step 1 — retrieve
    hits = retrieve(question, lang_filter=lang_filter, n=n_results)
    if not hits:
        return {
            "answer"  : "No relevant circulars found in the vector store for your question.",
            "sources" : [],
            "question": question,
        }

    # Step 2 — build chain
    llm    = get_llm(api_key)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM),
        ("human",  _HUMAN),
    ])
    chain = prompt | llm | StrOutputParser()

    # Step 3 — invoke
    answer = chain.invoke({
        "context" : _build_context(hits),
        "question": question,
    })

    return {
        "answer"  : answer.strip(),
        "sources" : hits,
        "question": question,
    }


# ── CLI smoke test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("Set GROQ_API_KEY first:  export GROQ_API_KEY=gsk_...")
        sys.exit(1)

    TEST_QUESTIONS = [
        "What are the salary revision circulars for 2025?",
        "Which circulars relate to leave for public officers?",
        "What is circular 10/2025 about?",
        "Are there circulars with deadlines in 2026?",
        "Tell me about festival advance payments",
    ]

    for q in TEST_QUESTIONS:
        print(f"\n{'='*62}")
        print(f"Q: {q}")
        res = answer_question(q, api_key)
        print(f"\nA: {res['answer'][:300]}...")
        print(f"\nTop sources:")
        for s in res["sources"][:3]:
            print(f"  {s['relevance_score']:5.1f}%  [{s['language']}]  "
                  f"{s['circular_number']}  {s['topic'][:50]}")
