# -*- coding: utf-8 -*-

import unittest

from src.pdf_report import (
    _enhance_semantic_blocks,
    _normalize_for_pdf,
    _replace_emojis_with_icons,
    _resolve_font_face,
)


class TestPdfReportEmojiIcons(unittest.TestCase):
    def test_resolve_font_face_returns_empty_css(self) -> None:
        css = _resolve_font_face()

        self.assertEqual(css, '')

    def test_heading_css_uses_unified_sans_cjk_and_normal_stretch(self) -> None:
        from src.pdf_report import CSS

        self.assertIn('font-family: "Noto Sans CJK SC"', CSS)
        self.assertIn('"Noto Sans CJK SC"', CSS)
        self.assertIn('font-stretch: normal;', CSS)
        self.assertIn('sans-serif;', CSS)

    def test_normalize_warning_variation_selector(self) -> None:
        self.assertEqual(_normalize_for_pdf("⚠️ 风险"), "⚠ 风险")

    def test_replace_report_emojis_with_svg_icons(self) -> None:
        html = (
            "<p>📰 重要信息速览</p>"
            "<p>💭 舆情情绪</p>"
            "<p>📱 社交情绪摘要</p>"
            "<p>📢 最新动态</p>"
            "<p>📌 核心结论</p>"
            "<p>💡 量价提示</p>"
            "<p>📍 操作点位</p>"
            "<p>🚨 风险警报</p>"
            "<p>✨ 利好催化</p>"
            "<p>🆕 空仓者</p>"
            "<p>💼 持仓者</p>"
            "<p>🔵 次优买入点</p>"
            "<p>🛑 止损位</p>"
            "<p>🎊 目标位</p>"
            "<p>💰 仓位建议</p>"
            "<p>⚠ 风险</p>"
        )

        out = _replace_emojis_with_icons(html)

        self.assertEqual(out.count("<img"), 16)
        self.assertNotIn("<p>📰 ", out)
        self.assertNotIn("<p>💼 ", out)
        self.assertNotIn("<p>🛑 ", out)
        self.assertIn('alt="💰"', out)
        self.assertIn('alt="🆕" class="emoji-icon emoji-icon--label"', out)
        self.assertIn('alt="💼" class="emoji-icon emoji-icon--label"', out)

    def test_enhance_semantic_blocks_styles_labels(self) -> None:
        html = (
            "<h1>今日策略结论</h1>"
            "<p>市场状态：中性偏弱</p>"
            "<p>今日最值得关注：</p>"
            "<h2>闪迪</h2>"
            "<p>逻辑：等待回踩确认</p>"
            "<p>失效条件：跌破关键均线</p>"
        )

        out = _enhance_semantic_blocks(html)

        self.assertIn('<h1 class="section-title">今日策略结论</h1>', out)
        self.assertIn('<p class="lead-row"><span class="lead-label">市场状态：</span>中性偏弱</p>', out)
        self.assertIn('<p class="list-intro">今日最值得关注：</p>', out)
        self.assertIn('<h2 class="stock-title">闪迪</h2>', out)
        self.assertIn('<span class="field-label">逻辑：</span>等待回踩确认', out)
        self.assertIn('<span class="field-label">失效条件：</span>跌破关键均线', out)


if __name__ == "__main__":
    unittest.main()
