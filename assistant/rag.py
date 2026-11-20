"""
RAG over the project's own rulebook (docs/*.md).

Build the index once:    python assistant/rag.py
Then search_playbooks() returns the most relevant passages for a question.

Chroma's built-in embedding model (a small, free model that runs on your
laptop) turns each passage into numbers so passages with similar meaning
can be found, even when they don't share the same words.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import chromadb

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INDEX = ROOT / "local/chroma"
COLLECTION = "playbooks"


def _chunks(path: Path) -> list[tuple[str, str]]:
    """Split a markdown file at its ## headings. Returns (section title, text) pairs."""
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"\n(?=## )", text)
    out = []
    for part in parts:
        title = part.strip().splitlines()[0].lstrip("# ").strip()
        if part.strip():
            out.append((title, part.strip()))
    return out


def build_index() -> None:
    client = chromadb.PersistentClient(path=str(INDEX))
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.create_collection(COLLECTION)
    ids, docs, metas = [], [], []
    for path in sorted(DOCS.glob("*.md")):
        for i, (title, text) in enumerate(_chunks(path)):
            ids.append(f"{path.stem}-{i}")
            docs.append(text)
            metas.append({"source": f"docs/{path.name}", "section": title})
    col.add(ids=ids, documents=docs, metadatas=metas)
    print(f"Indexed {len(ids)} passages from {DOCS}")


def search_playbooks(query: str) -> str:
    """Search the rule catalog, exception SOP and KPI definitions. Returns the 3 most relevant passages with their source."""
    col = chromadb.PersistentClient(path=str(INDEX)).get_collection(COLLECTION)
    res = col.query(query_texts=[query], n_results=3)
    hits = [
        {"source": m["source"], "section": m["section"], "text": d}
        for d, m in zip(res["documents"][0], res["metadatas"][0])
    ]
    return json.dumps(hits, indent=2)


if __name__ == "__main__":
    build_index()
    print(search_playbooks("what does a fee mismatch mean and what should I do"))
