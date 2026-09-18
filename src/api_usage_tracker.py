from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / 'state' / 'api_usage_stats.json'
STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = RLock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _load() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {'providers': {}, 'updated_at': _utc_now_iso()}
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return {'providers': {}, 'updated_at': _utc_now_iso()}
        data.setdefault('providers', {})
        return data
    except Exception:
        return {'providers': {}, 'updated_at': _utc_now_iso()}


def _save(data: Dict[str, Any]) -> None:
    data['updated_at'] = _utc_now_iso()
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def _mask_key(key: Optional[str]) -> str:
    if not key:
        return 'default'
    key = str(key).strip()
    if len(key) <= 10:
        return key
    return f"{key[:8]}...{key[-4:]}"


def mask_key_for_display(key: Optional[str]) -> str:
    return _mask_key(key)


def _get_provider(data: Dict[str, Any], provider: str) -> Dict[str, Any]:
    providers = data.setdefault('providers', {})
    node = providers.setdefault(provider, {})
    node.setdefault('totals', {
        'requests': 0,
        'success': 0,
        'failure': 0,
        'last_success_at': None,
        'last_failure_at': None,
        'last_error': None,
    })
    node.setdefault('keys', {})
    return node


def _get_key_node(provider_node: Dict[str, Any], key: Optional[str]) -> Dict[str, Any]:
    masked = _mask_key(key)
    keys = provider_node.setdefault('keys', {})
    node = keys.setdefault(masked, {
        'requests_total': 0,
        'success_total': 0,
        'failure_total': 0,
        'requests_today': 0,
        'day': _today_utc(),
        'last_used_at': None,
        'last_success_at': None,
        'last_failure_at': None,
        'last_error': None,
        'last_status': None,
    })
    today = _today_utc()
    if node.get('day') != today:
        node['day'] = today
        node['requests_today'] = 0
    return node


def record_api_result(provider: str, key: Optional[str] = None, *, success: bool, error: Optional[str] = None) -> None:
    with _LOCK:
        data = _load()
        provider_node = _get_provider(data, provider)
        totals = provider_node['totals']
        key_node = _get_key_node(provider_node, key)
        now = _utc_now_iso()

        totals['requests'] += 1
        key_node['requests_total'] += 1
        key_node['requests_today'] += 1
        key_node['last_used_at'] = now
        key_node['last_status'] = 'success' if success else 'failure'

        if success:
            totals['success'] += 1
            totals['last_success_at'] = now
            key_node['success_total'] += 1
            key_node['last_success_at'] = now
        else:
            totals['failure'] += 1
            totals['last_failure_at'] = now
            totals['last_error'] = error
            key_node['failure_total'] += 1
            key_node['last_failure_at'] = now
            key_node['last_error'] = error

        _save(data)


def load_usage_stats() -> Dict[str, Any]:
    with _LOCK:
        return _load()
