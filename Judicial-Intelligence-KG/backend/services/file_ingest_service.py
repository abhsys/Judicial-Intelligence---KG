from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader


class FileIngestService:
    SUPPORTED_SUFFIXES = {".pdf", ".txt"}
    MAX_FILE_BYTES = 10 * 1024 * 1024

    def validate(self, filename: str, content: bytes) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise ValueError("Only .pdf and .txt files are supported.")
        if not content:
            raise ValueError("Uploaded file is empty.")
        if len(content) > self.MAX_FILE_BYTES:
            raise ValueError(
                f"File too large. Max size is {self.MAX_FILE_BYTES // (1024 * 1024)} MB."
            )
        return suffix

    def sha256(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def extract_text(self, suffix: str, content: bytes) -> str:
        if suffix == ".txt":
            return self._extract_txt(content)
        if suffix == ".pdf":
            return self._extract_pdf(content)
        raise ValueError("Unsupported file type.")

    def _extract_txt(self, content: bytes) -> str:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")
        return " ".join(text.split())

    def _extract_pdf(self, content: bytes) -> str:
        # pypdf accepts file-like objects.
        from io import BytesIO

        reader = PdfReader(BytesIO(content))
        pages_text: list[str] = []
        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            if page_text:
                pages_text.append(page_text)
        text = "\n".join(pages_text)
        return " ".join(text.split())
