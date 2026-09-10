"""Export a saved chat to Word (.docx), PDF, or plain text."""
import io
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

# Unicode punctuation and symbols mapped to safe Latin-1 / ASCII equivalents
_PDF_FIX = {
    "\u2014": "--", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2022": "*", "\u2026": "...",
    "\u2192": "->", "\u00a0": " ", "\u2705": "[ok]", "\u26a0": "[!]",
    "\u2714": "[x]", "\u2716": "[x]", "\u25aa": "*", "\u25cf": "*",
}


def _pdf_safe(s: str) -> str:
    """Sanitize string for PDF rendering by substituting Unicode glyphs and soft-wrapping long tokens."""
    for k, v in _PDF_FIX.items():
        s = s.replace(k, v)
    # Encode with replacement to prevent crashes on exotic unicode
    s = s.encode("latin-1", "replace").decode("latin-1")
    # Soft wrap very long unbroken tokens (e.g. raw long URLs)
    return re.sub(r"(\S{60})(?=\S)", r"\1 ", s)


def export_txt(title: str, mode: str, messages: List[Dict[str, Any]]) -> bytes:
    """Export conversation to plain text format."""
    lines = [f"AIRA CONVERSATION — {mode} mode", f"Exported: {datetime.now():%Y-%m-%d %H:%M}", "=" * 60, ""]
    for m in messages:
        who = "YOU" if m.get("role") == "user" else "AIRA"
        lines.append(f"[{who}]")
        lines.append(m.get("text", ""))
        if m.get("sources"):
            lines.append("Sources:")
            lines.extend(f"  {i}. {s}" for i, s in enumerate(m["sources"], 1))
        if m.get("tools"):
            lines.append(f"(steps: {' -> '.join(m['tools'])})")
        lines.append("")
    return ("\n".join(lines)).encode("utf-8")


def export_docx(title: str, mode: str, messages: List[Dict[str, Any]]) -> bytes:
    """Export conversation to Microsoft Word (.docx) format."""
    from docx import Document

    doc = Document()
    doc.add_heading("Aira Conversation", 0)
    p = doc.add_paragraph()
    p.add_run(f"Mode: {mode}   |   Exported: {datetime.now():%Y-%m-%d %H:%M}").italic = True
    for m in messages:
        who = "You" if m.get("role") == "user" else "Aira"
        doc.add_heading(who, level=2)
        text = m.get("text", "")
        for chunk in text.split("\n\n"):
            c = chunk.strip()
            if c.startswith("```"):
                c = c.strip("`").strip()
            if c:
                doc.add_paragraph(c)
        if m.get("tools"):
            doc.add_paragraph(f"Steps: {' -> '.join(m['tools'])}").italic = True
        if m.get("sources"):
            doc.add_heading("Sources", level=3)
            for i, s in enumerate(m["sources"], 1):
                doc.add_paragraph(f"{i}. {s}", style="List Number")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def export_pdf(title: str, mode: str, messages: List[Dict[str, Any]]) -> bytes:
    """Export conversation to formatted PDF with graceful font and layout fallback."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 10, _pdf_safe("Aira Conversation"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("helvetica", "I", 9)
    pdf.cell(0, 6, _pdf_safe(f"Mode: {mode}   |   Exported: {datetime.now():%Y-%m-%d %H:%M}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    for m in messages:
        who = "You" if m.get("role") == "user" else "Aira"
        pdf.set_font("helvetica", "B", 12)
        pdf.set_text_color(14, 116, 144)
        pdf.cell(0, 8, _pdf_safe(who), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("helvetica", "", 10)
        for chunk in m.get("text", "").split("\n"):
            c = chunk.rstrip()
            if c.strip().startswith("```"):
                continue
            pdf.multi_cell(0, 5.5, _pdf_safe(c) if c.strip() else "", new_x="LMARGIN", new_y="NEXT")
        if m.get("tools"):
            pdf.set_font("helvetica", "I", 8.5)
            pdf.multi_cell(0, 4.5, _pdf_safe(f"Steps: {' -> '.join(m['tools'])}"), new_x="LMARGIN", new_y="NEXT")
        if m.get("sources"):
            pdf.set_font("helvetica", "B", 10)
            pdf.cell(0, 6, "Sources", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("helvetica", "", 9)
            for i, s in enumerate(m["sources"], 1):
                pdf.multi_cell(0, 4.8, _pdf_safe(f"{i}. {s}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    return bytes(pdf.output())


def export_chat(title: str, mode: str, messages: List[Dict[str, Any]], fmt: str) -> Tuple[bytes, str, str]:
    """Returns (file_bytes, filename, mime_type)."""
    safe_title = re.sub(r"[^a-zA-Z0-9-_]+", "-", (title or "chat").strip())[:40].strip("-")
    mode_tag = mode.lower().replace("_", "-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if fmt == "pdf":
        return export_pdf(title, mode, messages), f"aira-{mode_tag}-{stamp}.pdf", "application/pdf"
    if fmt == "docx":
        return export_docx(title, mode, messages), f"aira-{mode_tag}-{stamp}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return export_txt(title, mode, messages), f"aira-{mode_tag}-{stamp}.txt", "text/plain"
