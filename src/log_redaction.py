"""Redact credentials that may be embedded in log messages or URLs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_TELEGRAM_BOT_URL_RE = re.compile(
    r"(?i)(https?://api\.telegram\.org/bot)[^/\s?#]+"
)
_TELEGRAM_BOT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])\d{5,}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_REDACTED = "[REDACTED]"


def redact_sensitive_text(value: Any, *, secrets: Iterable[Any] = ()) -> str:
    """Return log-safe text with known secrets and Telegram bot tokens removed."""
    text = str(value)
    for secret in secrets:
        secret_text = str(secret or "")
        if secret_text:
            text = text.replace(secret_text, _REDACTED)
    text = _TELEGRAM_BOT_URL_RE.sub(r"\1[REDACTED]", text)
    return _TELEGRAM_BOT_TOKEN_RE.sub(_REDACTED, text)
