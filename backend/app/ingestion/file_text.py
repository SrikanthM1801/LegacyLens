"""
Plain text from document files (SharePoint), so they can go through the normal pipeline.

Supported: .docx .pptx .xlsx .pdf .txt .md .csv .json .html/.htm. Anything else raises
UnsupportedFile (recorded as `unsupported` in the source registry and audit, not an error).
Old binary formats (.doc .ppt .xls) are not supported. Output is capped at MAX_CHARS so one
huge file cannot flood the index.
"""

from __future__ import annotations

import html
import io
import re
from pathlib import PurePosixPath

MAX_CHARS = 200_000
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log"}
SUPPORTED = TEXT_EXTS | {".docx", ".pptx", ".xlsx", ".pdf", ".html", ".htm"}
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


class UnsupportedFile(ValueError):
    """We don't read this file type."""


def extension(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


def is_supported(name: str) -> bool:
    return extension(name) in SUPPORTED


def _docx(data: bytes) -> str:
    import docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(dict.fromkeys(cells)))  # merged cells repeat; keep once
    return "\n".join(parts)


def _pptx(data: bytes) -> str:
    from pptx import Presentation

    parts = []
    for i, slide in enumerate(Presentation(io.BytesIO(data)).slides, 1):
        texts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            texts.append("Notes: " + slide.notes_slide.notes_text_frame.text)
        if texts:
            parts.append(f"Slide {i}: " + "\n".join(texts))
    return "\n\n".join(parts)


def _xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            parts.append(f"Sheet {ws.title}:\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(parts)


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages)


def _html(data: bytes) -> str:
    text = _SCRIPT.sub("", data.decode("utf-8", errors="replace"))
    text = re.sub(r"<\s*(br|/p|/div|/li|/h\d|/tr)\s*/?>", "\n", text, flags=re.IGNORECASE)
    return html.unescape(_TAG.sub("", text))


def extract_text(name: str, data: bytes) -> str:
    ext = extension(name)
    if ext in TEXT_EXTS:
        text = data.decode("utf-8", errors="replace")
    elif ext == ".docx":
        text = _docx(data)
    elif ext == ".pptx":
        text = _pptx(data)
    elif ext == ".xlsx":
        text = _xlsx(data)
    elif ext == ".pdf":
        text = _pdf(data)
    elif ext in (".html", ".htm"):
        text = _html(data)
    else:
        raise UnsupportedFile(f"{ext or 'no extension'} files are not read")
    lines = (line.strip() for line in text.replace("\r", "").splitlines())
    return "\n".join(line for line in lines if line)[:MAX_CHARS]
