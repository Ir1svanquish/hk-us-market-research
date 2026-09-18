# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


HK_INDUSTRY_PATH = Path('/tmp/futu_hk_full.json')
US_INDUSTRY_PATH = Path('/tmp/futu_us_full.json')
HK_CONCEPTS_PATH = Path('/tmp/futu_hk_concepts_full.json')
US_CONCEPTS_PATH = Path('/tmp/futu_us_concepts_full.json')


HK_CONCEPT_MAP: dict[str, list[str]] = {
    '半导体': ['存儲概念', '功率半導體', '晶片股'],
    '半导体设备与材料': ['存儲概念', '功率半導體', '晶片股'],
    '互动媒体及服务': ['短視頻概念股'],
    '应用软件': ['AI次新股', '短視頻概念股'],
    '汽车': ['無人駕駛', '鋰電池', '激光雷達概念'],
    '电子零件': ['蘋果概念', '光通信'],
    '互联网服务及基础设施': ['AI次新股', '短視頻概念股'],
    '医疗设备及用品': ['創新藥', '醫療器械概念'],
    '黄金及贵金属': ['黃金概念', '稀土概念'],
    '能源储存装置': ['儲能概念股', '鋰電池'],
    '消费电子产品': ['蘋果概念', 'OLED概念'],
    '电讯网路基建设施': ['光通信'],
}

US_CONCEPT_MAP: dict[str, list[str]] = {
    '半导体': ['功率半導體'],
    '半导体设备与材料': ['功率半導體', '光通信'],
    '计算机硬件': ['光通信', 'SaaS概念'],
    '软件基础设施': ['SaaS概念'],
    '互联网内容与信息': ['SaaS概念'],
    '资本市场': ['穩定幣槪念'],
    '电气设备及零件': ['鋰電池', '電力股', '核電'],
    '独立电力生产商': ['電力股', '核電', '綠電概念'],
    '生物技术': ['創新藥概念', 'AI醫療概念股', '生物醫藥', '互聯網醫療'],
    '黄金': ['黃金股'],
    '白银': ['白銀概念'],
    '铜': ['銅礦股', '光通信'],
    '航空航天与国防': ['航空股'],
    '汽车和卡车经销': ['汽車經銷商', '鋰電池'],
    '专用工业机械': ['核電'],
    '货运': ['物流'],
}

SIMPLIFIED_TEXT_MAP: dict[str, str] = {
    '存儲概念': '存储概念',
    '功率半導體': '功率半导体',
    '晶片股': '芯片股',
    '短視頻概念股': '短视频概念股',
    'AI次新股': 'AI次新股',
    '無人駕駛': '无人驾驶',
    '鋰電池': '锂电池',
    '激光雷達概念': '激光雷达概念',
    '蘋果概念': '苹果概念',
    '創新藥': '创新药',
    '醫療器械概念': '医疗器械概念',
    '黃金概念': '黄金概念',
    '儲能概念股': '储能概念股',
    '穩定幣槪念': '稳定币概念',
    '電力股': '电力股',
    '核電': '核电',
    '綠電概念': '绿电概念',
    '創新藥概念': '创新药概念',
    'AI醫療概念股': 'AI医疗概念股',
    '生物醫藥': '生物医药',
    '互聯網醫療': '互联网医疗',
    '黃金股': '黄金股',
    '白銀概念': '白银概念',
    '銅礦股': '铜矿股',
    '汽車經銷商': '汽车经销商',
    '腦機接口概念': '脑机接口概念',
    '醫藥外包概念': '医药外包概念',
    '騰訊概念': '腾讯概念',
    '體育用品': '体育用品',
    '水務股': '水务股',
    '粤港澳大湾区': '粤港澳大湾区',
    '港口運輸股': '港口运输股',
    '奢侈品品牌股': '奢侈品品牌股',
    '燃氣股': '燃气股',
    '電信股': '电信股',
    '節假日概念股': '节假日概念股',
    '石油與天然氣': '石油与天然气',
    '中特估-國企': '中特估-国企',
    '中資券商股': '中资券商股',
    '航空航天與國防': '航空航天与国防',
    '半導體': '半导体',
    '設備與材料': '设备与材料',
    '黃金': '黄金',
    '白銀': '白银',
    '醫療': '医疗',
    '視頻': '视频',
    '聯網': '联网',
    '網絡': '网络',
    '網路': '网络',
    '機械': '机械',
    '電氣': '电气',
    '電子': '电子',
    '廣播': '广播',
    '運輸': '运输',
    '觀察': '观察',
    '強化': '强化',
    '分化': '分化',
    '退潮': '退潮',
}


@dataclass
class RotationItem:
    name: str
    change_pct: float
    up_count: int
    flat_count: int
    down_count: int
    total_count: int
    leader_stock: str
    leader_change_pct: float
    breadth: float
    down_ratio: float
    leader_excess: float
    final_score: float
    weak_score: float
    status: str
    matched_concepts: list[str]


@dataclass
class RotationSection:
    main_lines: list[RotationItem]
    secondary_lines: list[RotationItem]
    avoid_lines: list[RotationItem]
    watchlist: list[RotationItem]


def _to_float_pct(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace('%', '').replace('+', '')
    return float(text) if text else 0.0


def _to_int(value: Any) -> int:
    if value in (None, ''):
        return 0
    return int(value)


def _calc_sample_score(total_count: int) -> float:
    if total_count >= 50:
        return 1.0
    if total_count >= 20:
        return 0.7
    if total_count >= 8:
        return 0.4
    return 0.1


def _calc_leader_score(leader_excess: float) -> float:
    if leader_excess < 0:
        return 0.2
    if leader_excess <= 8:
        return 1.0
    if leader_excess <= 20:
        return 0.7
    return 0.3


def _to_simplified(text: str) -> str:
    value = text or ''
    for source, target in sorted(SIMPLIFIED_TEXT_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        value = value.replace(source, target)
    return value


def _market_paths(region: str) -> tuple[Path, Path] | tuple[None, None]:
    normalized = (region or '').lower()
    if normalized == 'hk':
        return HK_INDUSTRY_PATH, HK_CONCEPTS_PATH
    if normalized == 'us':
        return US_INDUSTRY_PATH, US_CONCEPTS_PATH
    return None, None


def _concept_map(region: str) -> dict[str, list[str]]:
    return HK_CONCEPT_MAP if (region or '').lower() == 'hk' else US_CONCEPT_MAP


def _load_rows(path: Path, plate_type: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    rows: list[dict[str, Any]] = []
    for page_block in payload.get('pages', []):
        for row in page_block.get('rows', []):
            name_key = 'sector_name' if plate_type == 'industry' else 'concept_name'
            change_key = 'sector_change_pct' if plate_type == 'industry' else 'concept_change_pct'
            rows.append(
                {
                    'name': row.get(name_key) or '',
                    'change_pct': _to_float_pct(row.get(change_key)),
                    'up_count': _to_int(row.get('up_count')),
                    'flat_count': _to_int(row.get('flat_count')),
                    'down_count': _to_int(row.get('down_count')),
                    'leader_stock': row.get('leader_stock') or '',
                    'leader_change_pct': _to_float_pct(row.get('leader_change_pct')),
                }
            )
    return rows


def _score_industry_rows(rows: list[dict[str, Any]]) -> list[RotationItem]:
    scored: list[RotationItem] = []
    for row in rows:
        total_count = row['up_count'] + row['flat_count'] + row['down_count']
        if total_count == 0:
            continue
        breadth = row['up_count'] / total_count
        down_ratio = row['down_count'] / total_count
        leader_excess = row['leader_change_pct'] - row['change_pct']
        sample_score = _calc_sample_score(total_count)
        leader_score = _calc_leader_score(leader_excess)
        final_score = (
            0.45 * row['change_pct']
            + 0.35 * ((breadth - 0.5) * 10)
            + 0.15 * sample_score
            + 0.05 * leader_score
        )
        if total_count < 5:
            final_score *= 0.4
        if row['leader_change_pct'] > 20 and breadth < 0.45:
            final_score *= 0.6
        if row['change_pct'] > 0 and row['down_count'] > row['up_count']:
            final_score *= 0.7

        weak_score = (
            0.5 * abs(min(row['change_pct'], 0))
            + 0.4 * (down_ratio * 10)
            + 0.1 * sample_score
        )
        scored.append(
            RotationItem(
                name=row['name'],
                change_pct=row['change_pct'],
                up_count=row['up_count'],
                flat_count=row['flat_count'],
                down_count=row['down_count'],
                total_count=total_count,
                leader_stock=row['leader_stock'],
                leader_change_pct=row['leader_change_pct'],
                breadth=round(breadth, 4),
                down_ratio=round(down_ratio, 4),
                leader_excess=round(leader_excess, 4),
                final_score=round(final_score, 4),
                weak_score=round(weak_score, 4),
                status='观察',
                matched_concepts=[],
            )
        )
    return scored


def _score_concept_rows(rows: list[dict[str, Any]]) -> list[RotationItem]:
    scored: list[RotationItem] = []
    for row in rows:
        total_count = row['up_count'] + row['flat_count'] + row['down_count']
        if total_count == 0:
            continue
        breadth = row['up_count'] / total_count
        down_ratio = row['down_count'] / total_count
        leader_excess = row['leader_change_pct'] - row['change_pct']
        sample_score = _calc_sample_score(total_count)
        leader_score = _calc_leader_score(leader_excess)
        final_score = (
            0.55 * row['change_pct']
            + 0.30 * ((breadth - 0.5) * 10)
            + 0.10 * sample_score
            + 0.05 * leader_score
        )
        if total_count < 5:
            final_score *= 0.5
        scored.append(
            RotationItem(
                name=row['name'],
                change_pct=row['change_pct'],
                up_count=row['up_count'],
                flat_count=row['flat_count'],
                down_count=row['down_count'],
                total_count=total_count,
                leader_stock=row['leader_stock'],
                leader_change_pct=row['leader_change_pct'],
                breadth=round(breadth, 4),
                down_ratio=round(down_ratio, 4),
                leader_excess=round(leader_excess, 4),
                final_score=round(final_score, 4),
                weak_score=0.0,
                status='观察',
                matched_concepts=[],
            )
        )
    return scored


def _calc_status(item: RotationItem) -> str:
    if item.final_score > 1.5 and item.breadth >= 0.6 and 0 <= item.leader_excess <= 8:
        return '强化'
    if item.final_score > 1.0 and (item.breadth < 0.6 or item.leader_excess > 8):
        return '分化'
    if item.down_ratio >= 0.6:
        return '退潮'
    if item.change_pct < 0 and item.down_ratio > 0.6:
        return '退潮'
    return '观察'


def _attach_concepts(
    industries: list[RotationItem],
    concepts: list[RotationItem],
    concept_map: dict[str, list[str]],
) -> None:
    for industry in industries:
        wanted = concept_map.get(industry.name, [])
        matched = [item for item in concepts if item.name in wanted and item.final_score > 0]
        matched.sort(key=lambda item: item.final_score, reverse=True)
        if matched:
            industry.matched_concepts = [item.name for item in matched[:3]]
        else:
            industry.matched_concepts = wanted[:3]
        industry.status = _calc_status(industry)


def _build_rotation_buckets(rows: list[RotationItem]) -> RotationSection:
    long_candidates = [row for row in rows if row.change_pct > 0 and row.breadth >= 0.5 and row.total_count >= 8]
    long_candidates.sort(key=lambda item: item.final_score, reverse=True)

    weak_candidates = [row for row in rows if row.change_pct < 0 and row.down_ratio > 0.55 and row.total_count >= 8]
    weak_candidates.sort(key=lambda item: item.weak_score, reverse=True)

    watchlist = [row for row in rows if row.change_pct > 0 and row.final_score > 0 and row.total_count < 8]
    watchlist.sort(key=lambda item: item.final_score, reverse=True)

    return RotationSection(
        main_lines=long_candidates[:3],
        secondary_lines=long_candidates[3:5],
        avoid_lines=weak_candidates[:3],
        watchlist=watchlist[:2],
    )


def _label(item: RotationItem) -> str:
    concept_text = ''
    if item.matched_concepts:
        concept_text = '（' + ' / '.join(_to_simplified(name) for name in item.matched_concepts) + '）'
    return f"{_to_simplified(item.name)}{concept_text} [{_to_simplified(item.status)}]"


def build_rotation_section(region: str) -> Optional[RotationSection]:
    industry_path, concept_path = _market_paths(region)
    if not industry_path or not concept_path:
        return None
    if not industry_path.exists() or not concept_path.exists():
        return None

    industries = _score_industry_rows(_load_rows(industry_path, 'industry'))
    concepts = _score_concept_rows(_load_rows(concept_path, 'concept'))
    _attach_concepts(industries, concepts, _concept_map(region))
    return _build_rotation_buckets(industries)


def build_rotation_markdown_lines(region: str) -> list[str]:
    section = build_rotation_section(region)
    if section is None:
        return []

    lines = ['# 板块轮动', '']
    blocks = [
        ('主线：', section.main_lines),
        ('次主线：', section.secondary_lines),
        ('回避方向：', section.avoid_lines),
        ('观察方向：', section.watchlist),
    ]
    for title, items in blocks:
        lines.append(title)
        if items:
            for item in items:
                if title == '回避方向：' and item.status == '观察':
                    item.status = '退潮'
                lines.append(f'* {_label(item)}')
        else:
            lines.append('* 暂无')
        lines.append('')
    return lines
