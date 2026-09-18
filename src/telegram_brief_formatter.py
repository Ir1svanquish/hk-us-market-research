# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from src.analyzer import AnalysisResult
from src.compact_report_formatter import select_core_observations
from src.top5_brief_stats import build_top5_brief_lines

_INVALID_BRIEF_VALUES = ('分析完成', '留意回撤与追高风险', '暂无', '无', 'n/a', 'none', '数据缺失', '无法判断')


def _opportunity_badge(score: int, top_score: int) -> str:
    gap = max(0, top_score - score)
    if gap <= 2:
        stars = 5
    elif gap <= 5:
        stars = 4
    elif gap <= 8:
        stars = 3
    elif gap <= 12:
        stars = 2
    else:
        stars = 1
    return '机会：' + ('⭐' * stars)


def _strength_badge(score: int, top_score: int) -> str:
    gap = max(0, top_score - score)
    if gap <= 3:
        bolts = 3
    elif gap <= 8:
        bolts = 2
    else:
        bolts = 1
    return '强度：' + ('⚡' * bolts)


def _risk_badge(score: int) -> str:
    if score >= 80:
        return '风险：⚠️⚠️'
    if score >= 65:
        return '风险：⚠️⚠️'
    if score >= 55:
        return '风险：⚠️'
    return '风险：⚠️⚠️⚠️'


def _pick_market_title(region: str) -> str:
    if region == 'us':
        return '📡 美股复盘与今日观察'
    if region == 'hk':
        return '📡 港股复盘与今日观察'
    return '📡 市场复盘与今日观察'


def _market_state(results: List[AnalysisResult]) -> str:
    if not results:
        return '震荡整理'
    avg = sum(getattr(r, 'sentiment_score', 0) for r in results) / len(results)
    if avg >= 70:
        return '风险偏好回升'
    if avg >= 55:
        return '分化整理'
    return '中性偏弱'


def _natural_sentence(value: str, limit: int = 60) -> str:
    text = (value or '').replace('\n', ' ').strip()
    if not text:
        return ''
    if len(text) <= limit:
        return text
    best = ''
    for sep in ('。', '；', '，', ',', ';'):
        parts = text.split(sep)
        current = ''
        for idx, part in enumerate(parts):
            candidate = (current + (sep if current else '') + part).strip()
            if len(candidate) > limit:
                break
            current = candidate
        if current and len(current) > len(best):
            best = current + ('' if current.endswith(('。', '！', '？')) else '。')
    if best:
        return best
    fallback = text[:limit]
    for ch in (' ', '，', ',', '；', ';', '。'):
        pos = fallback.rfind(ch)
        if pos > limit // 2:
            fallback = fallback[:pos]
            break
    return fallback.rstrip('（(').rstrip() + '。'


def _extract_reason(result: AnalysisResult) -> str:
    for field in ('buy_reason', 'analysis_summary', 'key_points'):
        value = getattr(result, field, None)
        if value:
            return _natural_sentence(str(value), 64)
    return ''


def _extract_risk(result: AnalysisResult) -> str:
    value = getattr(result, 'risk_warning', None)
    if value:
        return _natural_sentence(str(value), 64)
    return ''


def _is_valid_brief_text(text: str) -> bool:
    normalized = (text or '').strip().lower()
    if len(normalized) < 10:
        return False
    return not any(marker in normalized for marker in _INVALID_BRIEF_VALUES)


def format_telegram_brief(
    results: List[AnalysisResult],
    market_report: Optional[str] = None,
    region: str = 'cn',
) -> str:
    results = sorted(results, key=lambda x: getattr(x, 'sentiment_score', 0), reverse=True)
    selected = select_core_observations(results)
    top = []
    for item, reason, risk in selected:
        brief_reason = _natural_sentence(reason, 64)
        brief_risk = _natural_sentence(risk, 64)
        top.append((item, brief_reason, brief_risk))
    lines = [
        _pick_market_title(region),
        '',
        f'市场状态：{_market_state(results)}',
        '',
        '当前主线：',
    ]

    themes = []
    market_text = (market_report or '').replace('\n', ' ')
    for keyword in ('AI算力', '核能', '高速铜缆', '半导体', '云计算', '稳定币', '港股通'):
        if keyword in market_text:
            themes.append(keyword)
    if not themes:
        themes = ['强趋势龙头', '高景气主题', '事件驱动']
    for item in themes[:3]:
        lines.append(f'* {item}')

    lines.extend(['', '今日核心观察：', ''])
    display_top = top[:5]
    top_score = max((int(getattr(item, 'sentiment_score', 0) or 0) for item, _, _ in display_top), default=0)
    for idx, bundle in enumerate(display_top, start=1):
        item, reason, risk = bundle
        name = getattr(item, 'name', '') or item.code
        score = int(getattr(item, 'sentiment_score', 0) or 0)
        lines.append(f'{idx}. {name}')
        lines.append(f' {_opportunity_badge(score, top_score)} ｜ {_strength_badge(score, top_score)} ｜ {_risk_badge(score)}')
        lines.append(f' 看点：{reason or "详见 PDF"}')
        lines.append('')

    stats_lines = build_top5_brief_lines(region, [item for item, _, _ in display_top])
    if stats_lines:
        lines.extend(stats_lines)
        lines.append('')

    lines.extend(['更多逻辑与风险细节请看附件 PDF。'])
    return '\n'.join(lines)[:1200]
