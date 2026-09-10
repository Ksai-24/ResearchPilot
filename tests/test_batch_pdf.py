"""Test suite for processing, indexing, and analyzing 50+ PDFs efficiently."""
import asyncio
import io
import sys
from pathlib import Path
from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira.files import DocumentIndex, doc_index, search_documents_tool


def make_pdf(title: str, topic: str, secret_code: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)
    pdf.cell(text=title)
    pdf.ln(10)
    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(
        w=0,
        h=6,
        text=(
            f"This is research document {title} covering the subject of {topic}.\n"
            f"Experimental observations indicate consistent results across multiple trials.\n"
            f"Critical identifier for this trial is: {secret_code}.\n"
            "Further analysis is recommended for longitudinal studies and cross-cohort evaluations."
        ),
    )
    return bytes(pdf.output())


async def run_tests():
    print("== 1. Generate & Index 50+ PDFs ==")
    test_index = DocumentIndex()

    # Create 55 PDFs
    for i in range(1, 56):
        topic = "Machine Learning Optimization" if i == 42 else f"Generic Topic #{i}"
        secret = "TARGET_ALPHA_42" if i == 42 else f"CODE_{i:03d}"
        data = make_pdf(f"Paper_{i:02d}.pdf", topic, secret)
        res = test_index.add_document(f"Paper_{i:02d}.pdf", data)
        assert res["ok"], f"Failed to index Paper_{i:02d}.pdf: {res}"

    print(f"  Successfully indexed {len(test_index.documents)} PDFs ({len(test_index.chunks)} total chunks)")
    assert len(test_index.documents) == 55

    print("\n== 2. Compact Catalog Summary ==")
    catalog = test_index.get_catalog_summary()
    print(f"  Catalog summary length: {len(catalog)} characters")
    # Must be compact (< 25,000 chars) so it easily fits within LLM context
    assert len(catalog) < 25_000
    assert "Paper_01.pdf" in catalog
    assert "Paper_42.pdf" in catalog
    print("  Catalog summary generation OK")

    print("\n== 3. Search & Retrieval Across 50+ PDFs ==")
    # Search for the specific secret identifier in Paper 42
    results = test_index.search("TARGET_ALPHA_42")
    assert len(results) > 0, "Failed to retrieve matching chunk"
    top_hit = results[0]
    print(f"  Top hit: {top_hit['document']} (page {top_hit['page']}) with score {top_hit['score']}")
    assert top_hit["document"] == "Paper_42.pdf"
    assert "TARGET_ALPHA_42" in top_hit["excerpt"]
    print("  Exact passage retrieved successfully with citation metadata!")

    print("\n== 4. Agent search_documents tool integration ==")
    # Populate global doc_index with Paper 42
    doc_index.add_document("Paper_42.pdf", make_pdf("Paper_42.pdf", "Genomics", "GENOME_KEY_99"))
    tool_res = await search_documents_tool("GENOME_KEY_99")
    assert tool_res["total_matches"] > 0
    assert tool_res["results"][0]["document"] == "Paper_42.pdf"
    print("  Agent search_documents tool OK")

    print("\nALL 50+ PDF INDEXING & ANALYSIS TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_tests())
