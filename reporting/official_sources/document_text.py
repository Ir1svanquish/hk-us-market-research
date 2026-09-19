"""Bounded text extraction for official HKEX PDF/HTML documents."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from pypdf import PdfReader


class DocumentExtractionError(RuntimeError):
    pass


def _clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sentences(text: str) -> List[str]:
    # PDF extractors preserve visual line wrapping.  Join those lines first so
    # one factual sentence is not emitted as several incomplete fragments.
    flattened = re.sub(r"\s*\n\s*", " ", text)
    chunks = re.split(r"(?<=[。！？.!?])\s+", flattened)
    return [re.sub(r"\s+", " ", item).strip() for item in chunks if len(item.strip()) >= 16]


def extract_key_facts(text: str, limit: int = 4) -> List[str]:
    keywords = (
        "held on",
        "interim results",
        "annual results",
        "profit warning",
        "dividend",
        "回购",
        "回購",
        "中期业绩",
        "中期業績",
        "股息",
        "认缴出资",
        "認繳出資",
        "投资",
        "投資",
        "配售",
        "供股",
        "停牌",
    )
    selected = []
    for sentence in _sentences(text):
        if any(keyword.casefold() in sentence.casefold() for keyword in keywords):
            selected.append(sentence[:500])
            if len(selected) >= limit:
                break
    return selected


class OfficialDocumentTextExtractor:
    def __init__(
        self,
        timeout: float = 15.0,
        max_bytes: int = 8 * 1024 * 1024,
        max_pages: int = 20,
        max_chars: int = 12000,
        session: Optional[requests.Session] = None,
    ):
        self.timeout = max(1.0, float(timeout))
        self.max_bytes = max(1024, int(max_bytes))
        self.max_pages = max(1, int(max_pages))
        self.max_chars = max(1000, int(max_chars))
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "daily-stock-analysis-reporting/0.1 (official document reader)"})

    def extract(self, url: str) -> Dict[str, Any]:
        host = (urlparse(url).hostname or "").lower()
        if not (host == "hkexnews.hk" or host.endswith(".hkexnews.hk")):
            raise DocumentExtractionError(f"unsupported document host: {host}")
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        content = response.content
        if len(content) > self.max_bytes:
            raise DocumentExtractionError(f"document exceeds {self.max_bytes} byte limit")
        content_type = (response.headers.get("Content-Type") or "").lower()
        if url.lower().endswith(".pdf") or "application/pdf" in content_type:
            reader = PdfReader(BytesIO(content))
            pages = reader.pages[: self.max_pages]
            text = "\n".join((page.extract_text() or "") for page in pages)
            document_type = "pdf"
        else:
            raw = response.text
            text = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
            text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
            document_type = "html"
        text = _clean_text(text)[: self.max_chars]
        if not text:
            raise DocumentExtractionError("document has no extractable text")
        return {
            "status": "ok",
            "document_type": document_type,
            "bytes": len(content),
            "text": text,
            "key_facts": extract_key_facts(text),
        }
