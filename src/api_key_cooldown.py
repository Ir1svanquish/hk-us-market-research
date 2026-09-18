from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / 'state' / 'api_key_cooldowns.json'
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = RLock()


def _load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save(data: dict) -> None:
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def _load_probe_failures() -> dict:
    data = _load()
    return data.get('_probe_failures', {})


def _save_probe_failures(failures: dict) -> None:
    data = _load()
    if failures:
        data['_probe_failures'] = failures
    else:
        data.pop('_probe_failures', None)
    _save(data)


def is_cooled_down(provider: str, key: str) -> bool:
    with _LOCK:
        data = _load()
        entry: Any = data.get(provider, {}).get(key)
        if not entry:
            return False
        if isinstance(entry, dict):
            if entry.get('status') == 'quarantined':
                return True
            until = entry.get('until')
        else:
            # Backward-compatible timed cooldown entry.
            until = entry
        if not until:
            return False
        try:
            expiry = datetime.fromisoformat(until.replace('Z', '+00:00'))
        except (AttributeError, TypeError, ValueError):
            return False
        if expiry <= datetime.now(timezone.utc):
            data.get(provider, {}).pop(key, None)
            if not data.get(provider):
                data.pop(provider, None)
            _save(data)
            return False
        return True


def cooldown(provider: str, key: str, days: int = 30) -> None:
    with _LOCK:
        data = _load()
        data.setdefault(provider, {})[key] = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace('+00:00', 'Z')
        _save(data)


def quarantine(provider: str, key: str, reason: str = '') -> None:
    """Persistently isolate a credential until an operator restores it."""
    with _LOCK:
        data = _load()
        safe_reason = str(reason or 'authentication failed').replace(key, '[redacted]')[:240]
        data.setdefault(provider, {})[key] = {
            'status': 'quarantined',
            'reason': safe_reason,
            'quarantined_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        }
        failures = data.get('_probe_failures', {})
        provider_failures = failures.get(provider, {})
        provider_failures.pop(key, None)
        if not provider_failures:
            failures.pop(provider, None)
        if not failures:
            data.pop('_probe_failures', None)
        _save(data)


def restore(provider: str, key: str | None = None) -> int:
    with _LOCK:
        data = _load()
        if provider not in data:
            return 0
        if key:
            existed = 1 if key in data[provider] else 0
            data[provider].pop(key, None)
            if not data[provider]:
                data.pop(provider, None)
            failures = data.get('_probe_failures', {})
            provider_failures = failures.get(provider, {})
            provider_failures.pop(key, None)
            if not provider_failures:
                failures.pop(provider, None)
            if not failures:
                data.pop('_probe_failures', None)
            _save(data)
            return existed
        count = len(data[provider])
        data.pop(provider, None)
        failures = data.get('_probe_failures', {})
        failures.pop(provider, None)
        if not failures:
            data.pop('_probe_failures', None)
        _save(data)
        return count


def increment_probe_failure(provider: str, key: str) -> int:
    """
    记录一次探针失败，返回累计失败次数。
    只有达到 PROBE_FAILURE_THRESHOLD 时调用方才应执行冷却。
    """
    with _LOCK:
        failures = _load_probe_failures()
        fdata = failures.get(provider, {})
        current = fdata.get(key, 0) + 1
        fdata[key] = current
        failures[provider] = fdata
        _save_probe_failures(failures)
        return current


PROBE_FAILURE_THRESHOLD = 3


def reset_probe_failure(provider: str, key: str) -> None:
    """搜索成功时重置探针失败计数"""
    with _LOCK:
        failures = _load_probe_failures()
        fdata = failures.get(provider, {})
        if key in fdata:
            del fdata[key]
            if not fdata:
                failures.pop(provider, None)
            _save_probe_failures(failures)
