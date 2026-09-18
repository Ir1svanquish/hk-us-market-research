# -*- coding: utf-8 -*-

from src.analyzer import AnalysisResult
from src.compact_report_formatter import format_compact_report, select_core_observations


def _result(code: str, name: str, score: int, advice: str, trend: str, reason: str, risk: str) -> AnalysisResult:
    return AnalysisResult(
        code=code,
        name=name,
        sentiment_score=score,
        trend_prediction=trend,
        operation_advice=advice,
        analysis_summary=reason,
        risk_warning=risk,
    )


def test_compact_report_omits_market_environment_section() -> None:
    report = format_compact_report(
        [
            _result('0700.HK', '腾讯控股', 78, '买入', '看多', '等待回踩后继续观察', '跌破关键均线'),
            _result('9988.HK', '阿里巴巴-W', 32, '观望', '震荡', '暂不满足买入条件', '量价未企稳'),
        ],
        market_report='## 大盘复盘\n\n这里是旧的大盘环境内容。',
        region='hk',
    )

    assert '# 大盘环境' not in report
    assert '这里是旧的大盘环境内容。' not in report
    assert '# 板块轮动' in report


def test_compact_report_uses_rotation_section_when_available(monkeypatch) -> None:
    monkeypatch.setattr(
        'src.compact_report_formatter.build_rotation_markdown_lines',
        lambda region: [
            '# 板块轮动',
            '',
            '主线：',
            '* 半导体（存儲概念 / 功率半導體） [分化]',
            '',
            '次主线：',
            '* 互动媒体及服务（短視頻概念股） [观察]',
            '',
            '回避方向：',
            '* 煙草及電子煙股 [退潮]',
            '',
            '观察方向：',
            '* 光通信 [观察]',
            '',
        ],
    )

    report = format_compact_report(
        [
            _result('0700.HK', '腾讯控股', 78, '买入', '看多', '等待回踩后继续观察', '跌破关键均线'),
            _result('9988.HK', '阿里巴巴-W', 32, '观望', '震荡', '暂不满足买入条件', '量价未企稳'),
        ],
        market_report='## 大盘复盘\n\n这里是旧的大盘环境内容。',
        region='hk',
    )

    assert '当前主线：半导体 + 互动媒体及服务' in report
    assert '* 半导体（存儲概念 / 功率半導體） [分化]' in report
    assert '* 煙草及電子煙股 [退潮]' in report


def test_placeholder_summary_is_not_selected_as_core_observation() -> None:
    placeholder = _result('0981.HK', '中芯国际', 90, '买入', '看多', '分析完成', '跌破关键均线')
    meaningful = _result(
        '0700.HK',
        '腾讯控股',
        75,
        '观望',
        '震荡',
        '估值仍有支撑，但需要等待放量站稳压力位。',
        '跌破前低则逻辑失效。',
    )

    selected = select_core_observations([placeholder, meaningful])

    assert [item.code for item, _, _ in selected] == ['0700.HK']


def test_reason_keeps_complete_sentence_instead_of_cutting_at_80_characters() -> None:
    reason = '基本面与估值仍有支撑，短期量价结构正在改善；但只有放量站稳压力位后才算确认，否则应继续观望并严格控制仓位。'
    reason += '这是超过八十个字符后仍然必须保留的完整确认条件与风险说明。'
    report = format_compact_report(
        [_result('0700.HK', '腾讯控股', 78, '观望', '震荡', reason, '跌破前低则逻辑失效。')],
        region='hk',
    )

    assert reason in report
    assert '逻辑：' + reason in report


def test_meaningful_summary_keeps_data_gap_caveat() -> None:
    reason = '财务数据缺失，无法判断盈利增速；但技术面仍需等待放量站稳压力位。'

    report = format_compact_report(
        [_result('NVDA', '英伟达', 60, '观望', '震荡', reason, '跌破前低则逻辑失效。')],
        region='us',
    )

    assert '逻辑：' + reason in report


def test_other_stock_table_does_not_silently_drop_items_after_twenty() -> None:
    results = [
        _result(
            f'{index:04d}.HK',
            f'样本股票{index}',
            80 - index,
            '观望',
            '震荡',
            f'样本股票{index}等待量价确认。',
            '跌破前低则逻辑失效。',
        )
        for index in range(27)
    ]

    report = format_compact_report(results, region='hk')

    assert '样本股票26' in report
    assert sum(1 for line in report.splitlines() if line.startswith('| 样本股票')) == 22
