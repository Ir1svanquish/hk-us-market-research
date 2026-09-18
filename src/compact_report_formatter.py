# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional, Tuple

from src.analyzer import AnalysisResult
from src.sector_rotation import build_rotation_markdown_lines

_INVALID_EXACT_VALUES = {
    '',
    'none',
    'n/a',
    'na',
    'tbd',
    '待补充',
    '暂无',
    '无',
    '分析完成',
    'analysis completed',
    '留意回撤与追高风险',
    '数据缺失',
    '数据缺失，无法判断',
    '无法判断',
    '暂无数据',
    'data unavailable',
    'unable to determine',
}


def _is_meaningful(value) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    lowered = text.lower().strip("。.!！?？；;，, ")
    return lowered not in _INVALID_EXACT_VALUES


def _clean_text(value, default: str = '') -> str:
    if not _is_meaningful(value):
        return default
    text = str(value).replace('\n', ' ').strip()
    return text.strip()


def _market_state(results: List[AnalysisResult]) -> str:
    if not results:
        return '震荡整理'
    avg = sum(getattr(r, 'sentiment_score', 0) for r in results) / len(results)
    if avg >= 70:
        return '风险偏好回升'
    if avg >= 55:
        return '分化整理'
    return '中性偏弱'


def _extract_reason(result: AnalysisResult) -> str:
    # Risk warnings describe invalidation, not why a stock deserves attention.
    for field in ('buy_reason', 'analysis_summary', 'key_points'):
        value = _clean_text(getattr(result, field, None))
        if value:
            # Keep the complete reasoning. Character slicing can remove the
            # confirmation condition, stop condition, or the end of a sentence.
            return value
    return ''


def _extract_action(result: AnalysisResult) -> str:
    value = _clean_text(getattr(result, 'operation_advice', '观望'))
    return value or '观望'


def select_core_observations(results: List[AnalysisResult]) -> List[Tuple[AnalysisResult, str, str]]:
    ordered = sorted(results, key=lambda x: getattr(x, 'sentiment_score', 0), reverse=True)
    selected: List[Tuple[AnalysisResult, str, str]] = []
    for item in ordered:
        reason = _extract_reason(item)
        risk = _clean_text(getattr(item, 'risk_warning', None), '跌破关键均线')
        if not reason:
            continue
        selected.append((item, reason, risk))
        if len(selected) >= 5:
            break
    return selected


def format_compact_report(
    results: List[AnalysisResult],
    market_report: Optional[str] = None,
    region: str = 'cn',
    appendix: Optional[str] = None,
) -> str:
    ordered = sorted(results, key=lambda x: getattr(x, 'sentiment_score', 0), reverse=True)
    selected = select_core_observations(results)
    top = [item for item, _, _ in selected]
    top_codes = {getattr(item, 'code', None) for item in top}
    others = [item for item in ordered if getattr(item, 'code', None) not in top_codes]

    themes = []
    market_text = (market_report or '').replace('\n', ' ')
    for keyword in ('AI算力', '核能', '高速铜缆', '半导体', '云计算', '稳定币', '港股通'):
        if keyword in market_text:
            themes.append(keyword)
    if not themes:
        themes = ['强趋势龙头', '高景气主题', '事件驱动']

    rotation_lines = build_rotation_markdown_lines(region)
    if rotation_lines:
        rotation_theme_pool: list[str] = []
        current_block = ''
        for item in rotation_lines:
            if item in ('主线：', '次主线：', '回避方向：', '观察方向：'):
                current_block = item
                continue
            if current_block not in ('主线：', '次主线：'):
                continue
            if not item.startswith('* '):
                continue
            label = item[2:]
            label = label.split(' [', 1)[0]
            label = label.split('（', 1)[0]
            label = label.strip()
            if label and label != '暂无' and label not in rotation_theme_pool:
                rotation_theme_pool.append(label)
        if rotation_theme_pool:
            themes = rotation_theme_pool[:3]

    risk_names = [getattr(x, 'name', x.code) for x in sorted(ordered, key=lambda x: getattr(x, 'sentiment_score', 0))[:3]]

    lines = [
        '# 今日策略结论',
        '',
        f'市场状态：{_market_state(ordered)}',
        '',
        f'当前主线：{" + ".join(themes[:3])}',
        '',
        '今日最值得关注：',
        '',
    ]
    for item, _, _ in selected[:5]:
        lines.append(f'* {getattr(item, "name", item.code)}')
    if not selected:
        lines.append('* 暂无具备完整分析逻辑的标的')
    lines.extend([
        '',
        '今日不建议重仓参与：',
        '',
    ])
    for name in risk_names:
        lines.append(f'* {name}')
    lines.extend([
        '',
        '今日策略：',
        '轻仓参与强趋势，不追高，优先盯紧主线龙头与量价配合。',
    ])
    lines.extend([''])
    if rotation_lines:
        lines.extend(rotation_lines)
    else:
        lines.extend([
            '# 板块轮动',
            '',
            '强势：',
        ])
        for item in themes[:3]:
            lines.append(f'* {item}')
        lines.extend(['', '转弱：', f'* {risk_names[0] if risk_names else "高位分化"}', '', '观察：', '* 事件驱动方向', ''])
    lines.extend(['# 核心观察', ''])

    for item, reason, risk in selected[:5]:
        name = getattr(item, 'name', item.code)
        lines.extend([
            f'## {name}',
            '',
            f'逻辑：{reason or "信号与催化仍需继续跟踪"}',
            '',
            f'观察重点：{_clean_text(getattr(item, "trend_prediction", None), "趋势延续性待确认")}',
            '',
            f'失效条件：{risk or "跌破关键均线"}',
            '',
        ])

    lines.extend(['# 其他股票简表', '', '| 股票 | 动作 | 理由 |', '| ---- | -- | ------ |'])
    for item in others:
        reason = _extract_reason(item)
        if not reason:
            reason = '等待更多催化'
        reason = reason.replace('\n', ' ').strip()
        lines.append(f'| {getattr(item, "name", item.code)} | {_extract_action(item)} | {reason} |')

    lines.extend(['', '# 附录：原始详细分析', ''])
    if appendix:
        if appendix and appendix.strip():
            lines.append(appendix)
    else:
        for item in ordered:
            lines.extend([
                f'## {getattr(item, "name", item.code)}({item.code})',
                '',
                f'- 动作：{_extract_action(item)}',
                f'- 评分：{getattr(item, "sentiment_score", "")}',
                f'- 趋势：{_clean_text(getattr(item, "trend_prediction", None))}',
                f'- 摘要：{_extract_reason(item) or "等待更多催化"}',
                '',
            ])
    return '\n'.join(lines)
