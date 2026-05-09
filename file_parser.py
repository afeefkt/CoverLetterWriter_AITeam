"""
file_parser.py — Extract plain text from PDF, DOCX, and TXT files.
Accepts both file paths and file-like objects (for Streamlit UploadedFile).
"""

from __future__ import annotations
from pathlib import Path
from typing import Union, BinaryIO


def extract_text_from_pdf(file) -> str:
    from pypdf import PdfReader
    reader = PdfReader(file)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def extract_text_from_docx(file) -> str:
    from docx import Document
    doc = Document(file)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def extract_text_from_txt(file) -> str:
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
