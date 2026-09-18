"""Small JSON cache used by V2 issuer maps."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional


class JsonFileCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        path = self.root / f"{key}.json"
        try:
            if ttl_seconds >= 0 and time.time() - path.stat().st_mtime > ttl_seconds:
                return None
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return None

    def set(self, key: str, payload: Any) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{key}.json"
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{key}.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_name, target)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        return target
