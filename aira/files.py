"""Extract text and build searchable multi-document indexes from uploaded files.

Supports:
- Memory-bounded streaming extraction for PDF, DOCX, DOC, TXT/MD
- 50+ PDF Multi-Document Indexing, chunking, and BM25-style keyword search
- Compact catalog summaries with caching so 50+ documents fit safely within LLM context windows
"""
import io
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_CHARS = 120_000
CHUNK_SIZE = 1_500
CHUNK_OVERLAP = 250

STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than",
    "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there",
    "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this",
    "those", "through", "to", "too", "under", "until", "up", "very", "was", "wasn't",
    "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's",
    "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom",
    "why", "why's", "with", "won't", "would", "wouldn't", "you", "you'd", "you'll",
    "you're", "you've", "your", "yours", "yourself", "yourselves"
}


try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # type: ignore

try:
    from docx import Document  # type: ignore
except ImportError:
    Document = None  # type: ignore


def _tokenize(text: str) -> List[str]:
    """Tokenize string into normalized lowercase keywords."""
    words = re.findall(r"[A-Za-z0-9_]{2,}", text.lower())
    return [w for w in words if w not in STOP_WORDS]


def extract_pdf(data: bytes) -> str:
    """Extract text from PDF using pypdf with robust stream fallback."""
    if PdfReader is not None:
        try:
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages:
                try:
                    txt = page.extract_text() or ""
                    if txt:
                        parts.append(txt)
                except Exception:
                    continue
            text = "\n".join(parts).strip()
            if text:
                return text
        except Exception:
            pass

    # Fallback: simple text stream recovery
    try:
        raw = data.decode("latin-1", errors="ignore")
        streams = re.findall(r"stream[\r\n]+([\s\S]*?)[\r\n]+endstream", raw)
        text_runs = []
        for s in streams:
            words = re.findall(r"\(([\w\s\.,;:!\?\-]{2,})\)", s)
            if words:
                text_runs.extend(words)
        if text_runs:
            return " ".join(text_runs)
    except Exception:
        pass
    return ""


def extract_docx(data: bytes) -> str:
    """Extract text from DOCX using python-docx with zipfile XML fallback."""
    if Document is not None:
        try:
            doc = Document(io.BytesIO(data))
            parts = [p.text for p in doc.paragraphs if p.text]
            for tbl in doc.tables:
                for row in tbl.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
            text = "\n".join(parts).strip()
            if text:
                return text
        except Exception:
            pass

    # Standard library fallback using zipfile and XML parsing
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            texts = [node.text for node in tree.iter() if node.tag.endswith("}t") and node.text]
            return " ".join(texts)
    except Exception:
        return ""


def extract_doc(data: bytes) -> str:
    """Legacy binary .doc: best-effort printable-run extraction."""
    for enc in ("utf-16", "latin-1", "utf-8"):
        text = data.decode(enc, errors="ignore")
        runs = re.findall(r"[\x20-\x7E\u00A0-\u024F\n]{6,}", text)
        cleaned = "\n".join(r.strip() for r in runs if len(r.strip()) >= 10)
        if len(cleaned) > 200:
            return cleaned
    return ""


def extract(name: str, data: bytes) -> Dict[str, Any]:
    ext = Path(name).suffix.lower()
    try:
        if ext == ".pdf":
            text = extract_pdf(data)
        elif ext == ".docx":
            text = extract_docx(data)
        elif ext == ".doc":
            text = extract_doc(data)
        elif ext in (".txt", ".md", ".text", ".csv", ".log", ".json"):
            text = data.decode("utf-8", errors="replace")
        else:
            return {"ok": False, "error": f"unsupported file type: {ext}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    text = text.strip()
    if not text:
        return {
            "ok": False,
            "error": "could not extract text (file may be scanned images or empty)",
        }
    truncated = len(text) > MAX_CHARS
    return {
        "ok": True,
        "name": name,
        "text": text[:MAX_CHARS],
        "chars": len(text),
        "truncated": truncated,
    }


class DocumentChunk:
    def __init__(self, doc_name: str, page_num: int, chunk_id: int, text: str):
        self.doc_name = doc_name
        self.page_num = page_num
        self.chunk_id = chunk_id
        self.text = text
        self.tokens = _tokenize(text)
        self.token_counts = Counter(self.tokens)


class DocumentIndex:
    """In-memory multi-document index designed to handle 50+ PDFs efficiently."""

    def __init__(self):
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.chunks: List[DocumentChunk] = []
        self._doc_freq: Dict[str, int] = Counter()
        self._cached_summary: Optional[str] = None

    def add_document(self, name: str, data: bytes) -> Dict[str, Any]:
        """Extract and index a document into searchable chunks with robust error recovery."""
        ext = Path(name).suffix.lower()
        extracted_pages = []

        if ext == ".pdf" and PdfReader is not None:
            try:
                reader = PdfReader(io.BytesIO(data))
                for i, page in enumerate(reader.pages, 1):
                    t = page.extract_text() or ""
                    if t.strip():
                        extracted_pages.append((i, t.strip()))
            except Exception:
                # Fallback to general extractor
                res = extract(name, data)
                if res.get("ok"):
                    extracted_pages.append((1, res.get("text", "")))
                else:
                    return {"ok": False, "name": name, "error": res.get("error", "PDF extraction failed")}
        else:
            res = extract(name, data)
            if not res.get("ok"):
                return res
            extracted_pages.append((1, res.get("text", "")))

        if not extracted_pages:
            return {"ok": False, "name": name, "error": "No readable text found"}

        # Invalidate cached catalog summary
        self._cached_summary = None

        # Build chunks with page tracking
        new_chunks = []
        full_text_pieces = []
        total_chars = 0
        for page_num, page_text in extracted_pages:
            full_text_pieces.append(page_text)
            total_chars += len(page_text)
            
            # Chunking with overlap
            pos = 0
            while pos < len(page_text):
                chunk_str = page_text[pos: pos + CHUNK_SIZE].strip()
                if chunk_str:
                    chunk = DocumentChunk(
                        doc_name=name,
                        page_num=page_num,
                        chunk_id=len(self.chunks) + len(new_chunks) + 1,
                        text=chunk_str,
                    )
                    new_chunks.append(chunk)
                pos += CHUNK_SIZE - CHUNK_OVERLAP

        # Update vocabulary frequencies
        for chunk in new_chunks:
            unique_terms = set(chunk.tokens)
            for t in unique_terms:
                self._doc_freq[t] += 1

        self.chunks.extend(new_chunks)

        full_text = "\n\n".join(full_text_pieces)
        # Concise executive summary for the multi-PDF catalog
        summary = full_text[:800].replace("\n", " ").strip()
        if len(full_text) > 800:
            summary += "…"

        self.documents[name] = {
            "name": name,
            "pages": len(extracted_pages),
            "chars": total_chars,
            "chunks": len(new_chunks),
            "summary": summary,
        }

        return {
            "ok": True,
            "name": name,
            "pages": len(extracted_pages),
            "chars": total_chars,
            "chunks": len(new_chunks),
            "summary": summary,
        }

    def search(self, query: str, top_k: int = 5, doc_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """BM25-style term-frequency scoring over indexed document chunks."""
        q_tokens = _tokenize(query)
        if not q_tokens or not self.chunks:
            return []

        N = max(1, len(self.chunks))
        k1 = 1.5
        b = 0.75
        avg_dl = sum(len(c.tokens) for c in self.chunks) / N

        scored_chunks = []
        for chunk in self.chunks:
            if doc_name and chunk.doc_name.lower() != doc_name.lower():
                continue

            dl = len(chunk.tokens)
            score = 0.0
            for t in q_tokens:
                tf = chunk.token_counts.get(t, 0)
                if tf > 0:
                    df = self._doc_freq.get(t, 1)
                    idf = math.log(1.0 + (N - df + 0.5) / (df + 0.5))
                    # BM25 formula
                    term_score = idf * ((tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (dl / avg_dl))))
                    score += term_score

            # Boost if query words match document name
            doc_lower = chunk.doc_name.lower()
            name_matches = sum(1 for t in q_tokens if t in doc_lower)
            if name_matches:
                score += name_matches * 2.0

            if score > 0:
                scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, chunk in scored_chunks[:top_k]:
            results.append({
                "score": round(score, 3),
                "document": chunk.doc_name,
                "page": chunk.page_num,
                "chunk_id": chunk.chunk_id,
                "excerpt": chunk.text,
            })
        return results

    def get_catalog_summary(self, max_docs: int = 60) -> str:
        """Create or return cached compact catalog summary of all uploaded documents."""
        if not self.documents:
            return ""

        if self._cached_summary is not None:
            return self._cached_summary

        lines = [f"--- UPLOADED DOCUMENTS CATALOG ({len(self.documents)} files indexed) ---"]
        for i, (name, meta) in enumerate(list(self.documents.items())[:max_docs], 1):
            lines.append(
                f"[{i}] {name} ({meta['pages']} pages, {meta['chars']} chars, {meta['chunks']} chunks)\n"
                f"    Preview: {meta['summary']}"
            )
        if len(self.documents) > max_docs:
            lines.append(f"... and {len(self.documents) - max_docs} more documents indexed.")
        lines.append("\nTip: Use tool `search_documents` with relevant keywords to read exact passages from any of these documents.")
        
        summary = "\n".join(lines)
        self._cached_summary = summary
        return summary

    def clear(self):
        """Reset all documents and clear cached summary."""
        self.documents.clear()
        self.chunks.clear()
        self._doc_freq.clear()
        self._cached_summary = None


# Global document store
doc_index = DocumentIndex()


async def search_documents_tool(query: str, doc_name: str = "") -> Dict[str, Any]:
    """Agent tool to search across indexed uploaded documents."""
    results = doc_index.search(query=query, top_k=6, doc_name=doc_name or None)
    if not results:
        return {
            "query": query,
            "results": [],
            "message": "No matching excerpts found in the uploaded documents. Try broader keywords.",
        }
    return {
        "query": query,
        "total_matches": len(results),
        "results": results,
    }
