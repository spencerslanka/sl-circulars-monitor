"""
build_vectorstore.py  —  Week 7, Step 1
========================================
Reads circulars.db (same DB from weeks 1-6)
→ builds rich text documents per circular
→ embeds them with a FREE local model (no API key)
→ saves ChromaDB to ./chroma_db/

Run ONCE before launching Streamlit:
    python build_vectorstore.py

Requirements:
    pip install chromadb sentence-transformers
"""

import sqlite3
import json
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions

# ── Config ────────────────────────────────────────────────────────────────────
DB_FILE     = "circulars.db"
CHROMA_DIR  = "./chroma_db"
COLLECTION  = "circulars"
EMBED_MODEL = "all-MiniLM-L6-v2"   # 90 MB, CPU-only, no API key needed


def load_circulars() -> list[dict]:
    """Load every summarised circular from your existing circulars.db."""
    if not Path(DB_FILE).exists():
        raise FileNotFoundError(
            f"\n❌  Cannot find '{DB_FILE}'.\n"
            "    Copy circulars.db into this folder and try again."
        )
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("""
        SELECT circular_number, issued_date, issued_by,
               topic, summary, key_instructions,
               applies_to, deadline, language
        FROM   circulars
        WHERE  summary IS NOT NULL
        ORDER  BY issued_date DESC, language
    """).fetchall()
    conn.close()

    out = []
    for r in rows:
        # key_instructions stored as JSON string in DB (from run_pipeline.py)
        try:
            ki = json.loads(r[5]) if r[5] else []
            if isinstance(ki, str):
                ki = [ki]
        except Exception:
            ki = [r[5]] if r[5] else []

        out.append({
            "circular_number"  : (r[0] or "").strip(),
            "issued_date"      : (r[1] or ""),
            "issued_by"        : (r[2] or ""),
            "topic"            : (r[3] or ""),
            "summary"          : (r[4] or ""),
            "key_instructions" : ki,
            "applies_to"       : (r[6] or ""),
            "deadline"         : (r[7] or ""),
            "language"         : (r[8] or "E"),
        })
    return out


def make_document(c: dict) -> str:
    """
    Build a rich searchable string for each circular.
    The more detail here, the better semantic search works.
    """
    lang   = "English" if c["language"] == "E" else "Sinhala (සිංහල)"
    instrs = "\n".join(f"  • {i}" for i in c["key_instructions"]) \
             if c["key_instructions"] else "  (none listed)"
    return f"""Circular Number: {c['circular_number']}
Language: {lang}
Date Issued: {c['issued_date'] or 'unknown'}
Issued By: {c['issued_by']}
Topic: {c['topic']}
Summary: {c['summary']}
Key Instructions:
{instrs}
Applies To: {c['applies_to'] or 'not specified'}
Deadline: {c['deadline'] or 'none'}"""


def build_vectorstore():
    print("\n" + "="*60)
    print("  Week 7 — Building ChromaDB Vector Store")
    print("="*60)

    # Load
    print(f"\n📂  Loading from '{DB_FILE}' ...")
    circulars = load_circulars()
    en = sum(1 for c in circulars if c["language"] == "E")
    si = sum(1 for c in circulars if c["language"] == "S")
    print(f"    {len(circulars)} circulars  (English: {en}, Sinhala: {si})")

    # ChromaDB + local embeddings
    print(f"\n🧠  Initialising ChromaDB  ({EMBED_MODEL})")
    print("    First run downloads ~90 MB model — takes ~60 sec")
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    try:
        client.delete_collection(COLLECTION)
        print("    Cleared previous collection")
    except Exception:
        pass
    collection = client.create_collection(
        name=COLLECTION,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Embed in batches
    print(f"\n📥  Embedding {len(circulars)} documents ...")
    BATCH = 32
    for start in range(0, len(circulars), BATCH):
        batch = circulars[start : start + BATCH]
        ids       = [f"{c['circular_number']}_{c['language']}_{start+i}"
                     for i, c in enumerate(batch)]
        documents = [make_document(c) for c in batch]
        metadatas = [{
            "circular_number"      : c["circular_number"],
            "issued_date"          : c["issued_date"],
            "issued_by"            : c["issued_by"],
            "topic"                : c["topic"],
            "applies_to"           : c["applies_to"],
            "deadline"             : c["deadline"] or "",
            "language"             : c["language"],
            "summary"              : c["summary"][:600],
            "key_instructions_json": json.dumps(c["key_instructions"],
                                                ensure_ascii=False)[:500],
        } for c in batch]
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        done = min(start + BATCH, len(circulars))
        filled = done * 30 // len(circulars)
        bar    = "█" * filled + "░" * (30 - filled)
        print(f"    [{bar}] {done}/{len(circulars)}", end="\r")

    print(f"\n\n🔍  Test: 'salary revision 2025' ...")
    r = collection.query(query_texts=["salary revision 2025"], n_results=3)
    for meta, dist in zip(r["metadatas"][0], r["distances"][0]):
        pct = round((1 - dist) * 100, 1)
        print(f"    {pct:5.1f}%  [{meta['language']}]  {meta['circular_number']}  "
              f"{meta['topic'][:55]}")

    print(f"\n✅  Done!  {collection.count()} vectors saved to {CHROMA_DIR}/")
    print("▶   Next:  streamlit run app.py\n")


if __name__ == "__main__":
    build_vectorstore()
