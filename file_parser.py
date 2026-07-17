"""
file_parser.py — Extract plain text from PDF, DOCX, and TXT files.
Accepts both file paths and file-like objects (for Streamlit UploadedFile).
"""

from __future__ import annotations
from pathlib import Path
from typing import Union, BinaryIO

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


def _check_size(file, label: str = "file") -> None:
    size = None
    if hasattr(file, "seek") and hasattr(file, "tell"):
        pos = file.tell()
        file.seek(0, 2)
        size = file.tell()
        file.seek(pos)
    elif hasattr(file, "size"):
        size = file.size
    if size is not None and size > MAX_FILE_BYTES:
        raise ValueError(
            f"{label} is {size / 1024 / 1024:.1f} MB — maximum is "
            f"{MAX_FILE_BYTES / 1024 / 1024:.0f} MB."
        )


def extract_text_from_pdf(file) -> str:
    _check_size(file, "PDF")
    from pypdf import PdfReader
    try:
        reader = PdfReader(file)
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p.strip() for p in pages if p.strip())
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF: {e}")


def extract_text_from_docx(file) -> str:
    _check_size(file, "DOCX")
    from docx import Document
    try:
        doc = Document(file)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception as e:
        raise RuntimeError(f"Failed to read DOCX: {e}")


def extract_text_from_txt(file) -> str:
    _check_size(file, "TXT")
    if hasattr(file, "read"):
        raw_bytes = file.read()
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return raw_bytes.decode("latin-1")
    return Path(file).read_text(encoding="utf-8", errors="replace")


def extract_text(file, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file)
    elif ext == ".docx":
        return extract_text_from_docx(file)
    elif ext in (".txt", ".md"):
        return extract_text_from_txt(file)
    else:
        raise ValueError(f"Unsupported file type: '{ext}'. Upload PDF, DOCX, or TXT.")


def extract_texts_from_uploads(uploaded_files: list) -> str:
    results = []
    for uf in uploaded_files:
        text = extract_text(uf, uf.name)
        if text.strip():
            results.append(f"--- {uf.name} ---\n{text.strip()}")
    return "\n\n".join(results)
