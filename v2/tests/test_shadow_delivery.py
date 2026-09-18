from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import mock

from src.formatters import markdown_to_html_document
from v2.shadow_delivery import (
    build_comparison_text,
    build_v2_digest,
    build_v2_telegram_digest,
    deliver_v2,
)


class ShadowDeliveryTests(unittest.TestCase):
    def test_us_digest_hides_internal_theme_placeholder_and_names_risk_stock(self) -> None:
        contract = {
            "stocks": [
                {
                    "identity": {"market": "us", "symbol": "NVDA", "name": "英伟达"},
                    "opportunity": {
                        "rank": 1,
                        "score": 72,
                        "themes": "半导体/算力",
                        "primary_catalyst": "数据中心需求增长",
                        "primary_risk": "财报后股价下跌4.57%，市场对指引反应平淡",
                    },
                    "execution": {"status": "等待触发", "standard_trade_card": {}},
                },
                {
                    "identity": {"market": "us", "symbol": "AAPL", "name": "苹果"},
                    "opportunity": {
                        "rank": 2,
                        "score": 69,
                        "themes": "未识别明确板块标签",
                    },
                    "execution": {"status": "研究关注", "standard_trade_card": {}},
                },
            ]
        }
        _subject, body = build_v2_digest(
            contract,
            {"markets": {"us": {"shadow_rolling_20": {}}}},
            "us",
            "2026-08-28",
            ledger_path=Path("/definitely/missing.sqlite3"),
        )
        self.assertIn("主线：** 半导体/算力", body)
        self.assertNotIn("未识别明确板块标签", body)
        self.assertIn("主要风险：** 英伟达（NVDA）：财报后股价下跌4.57%", body)

    def test_comparison_message_contains_pairing_progress_and_replacements(self) -> None:
        summary = {
            "markets": {
                "us": {
                    "rolling_20": {
                        "periods": 20,
                        "average_return_1d": 1.2,
                        "win_rate_1d": 55,
                        "average_return_5d": 3.4,
                        "win_rate_5d": 60,
                    },
                    "shadow_rolling_20": {
                        "periods": 2,
                        "average_return_1d": 1.8,
                        "win_rate_1d": 60,
                        "triggered": 2,
                        "trigger_eligible": 4,
                        "target_1_hits": 1,
                        "stop_hits": 0,
                    },
                    "comparison": {
                        "paired_periods_5d": 2,
                        "production_average_1d": 1.0,
                        "shadow_average_1d": 1.5,
                    },
                    "promotion_verdict": {"label": "证据不足"},
                    "latest_shadow_run": {
                        "production_top5_json": '["OLD", "SAME"]',
                        "overlap_ratio": 50,
                        "official_coverage": 100,
                        "relative_strength_coverage": 100,
                        "volume_coverage": 100,
                    },
                }
            }
        }
        contract = {
            "stocks": [
                {
                    "identity": {"market": "us", "symbol": "NEW", "name": "新版新增"},
                    "opportunity": {"rank": 1, "score": 80},
                    "execution": {"status": "可执行"},
                },
                {
                    "identity": {"market": "us", "symbol": "SAME", "name": "共同股票"},
                    "opportunity": {"rank": 2, "score": 75},
                    "execution": {"status": "等待触发"},
                },
                {
                    "identity": {"market": "us", "symbol": "OLD", "name": "旧版移出"},
                    "opportunity": {"rank": 8, "score": 50},
                    "execution": {"status": "研究关注"},
                },
            ]
        }
        text = build_comparison_text(summary, contract, "us", "2026-08-26")
        self.assertIn("5日配对 2/40期", text)
        self.assertIn("新版新增：新版新增", text)
        self.assertIn("移出：旧版移出", text)
        self.assertIn("8–12周", text)
        self.assertLessEqual(len(text), 1490)

    def test_v2_digest_uses_reader_facing_structure_and_dated_subject(self) -> None:
        contract = {
            "stocks": [
                {
                    "identity": {"market": "hk", "symbol": "HK00700", "name": "腾讯控股"},
                    "opportunity": {
                        "rank": 1,
                        "score": 78,
                        "themes": "互联网龙头",
                        "primary_catalyst": "业绩预期稳定，相对强弱改善。",
                        "primary_risk": "量能尚未确认。",
                    },
                    "execution": {
                        "status": "等待触发",
                        "standard_trade_card": {
                            "watch_condition": "放量突破关键结构位",
                            "invalidation_condition": "收盘跌破结构支撑",
                        },
                    },
                    "earnings_scenario": {
                        "status": "upcoming",
                        "trading_days_to_event": 2,
                        "timing": "8月29日收市后",
                    },
                }
            ]
        }
        validation = {
            "markets": {
                "hk": {
                    "shadow_rolling_20": {
                        "sample_5d": 12,
                        "win_rate_5d": 58.3,
                        "average_return_5d": 1.2,
                    }
                }
            }
        }
        hk_variables = {
            "signal": {"label": "中性", "summary": "南向小幅净流入。"},
            "southbound": {"net_5d_hkd_bn": -3.2},
            "market": {"distance_to_weak_side": 0.03},
        }

        subject, body = build_v2_digest(
            contract,
            validation,
            "hk",
            "2026-08-27",
            hk_variables=hk_variables,
            ledger_path=Path("/definitely/missing.sqlite3"),
        )

        self.assertEqual(subject, "港股复盘及机会日报｜2026-08-27｜中性")
        self.assertIn("今日市场结论", body)
        self.assertIn("腾讯控股｜机会分 78｜等待触发", body)
        self.assertIn("确认：** 放量突破关键结构位", body)
        self.assertIn("未来 3 个交易日事件", body)
        self.assertIn("成熟 5 日样本：** 12 条", body)
        self.assertNotIn("⭐", body)

        email_html = markdown_to_html_document(body)
        self.assertIn("<strong>状态：</strong> 中性", email_html)
        self.assertIn("<strong>确认：</strong> 放量突破关键结构位", email_html)
        self.assertNotIn("**状态：**", email_html)
        self.assertNotIn("**确认：**", email_html)

        telegram = build_v2_telegram_digest(
            subject,
            contract,
            validation,
            "hk",
            hk_variables=hk_variables,
        )
        self.assertIn("腾讯控股｜78分｜等待触发", telegram)
        self.assertIn("完整确认、失效条件与财报情景请看附件 PDF", telegram)
        self.assertLessEqual(len(telegram), 1490)

    @mock.patch("src.notification.NotificationService")
    def test_v2_delivery_sends_only_new_pdf_and_v2_digest(self, service_cls) -> None:
        contract = {
            "stocks": [
                {
                    "identity": {"market": "hk", "symbol": "HK00700", "name": "腾讯控股"},
                    "opportunity": {
                        "rank": 1,
                        "score": 78,
                        "themes": "互联网龙头",
                        "primary_catalyst": "业绩预期稳定。",
                        "primary_risk": "量能不足。",
                    },
                    "execution": {
                        "status": "等待触发",
                        "standard_trade_card": {
                            "watch_condition": "等待放量",
                            "invalidation_condition": "跌破支撑",
                        },
                    },
                }
            ]
        }
        validation = {"markets": {"hk": {"shadow_rolling_20": {"sample_5d": 0}}}}
        hk_variables = {
            "signal": {"label": "中性", "summary": "南向小幅净流入。"},
            "southbound": {"net_5d_hkd_bn": 1},
            "market": {"distance_to_weak_side": 1},
        }
        notifier = service_cls.return_value
        notifier._is_telegram_configured.return_value = True
        notifier._is_email_configured.return_value = True
        notifier.send_to_telegram.return_value = True
        notifier.send_telegram_document.return_value = True
        notifier.send_to_email.return_value = True
        notifier.get_all_email_receivers.return_value = ["reader@example.com"]

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            contract_path = root / "contract.json"
            validation_path = root / "validation.json"
            hk_path = root / "hk.json"
            old_pdf = root / "old.pdf"
            new_pdf = root / "new.pdf"
            state_path = root / "state.json"
            contract_path.write_text(__import__("json").dumps(contract), encoding="utf-8")
            validation_path.write_text(__import__("json").dumps(validation), encoding="utf-8")
            hk_path.write_text(__import__("json").dumps(hk_variables), encoding="utf-8")
            old_pdf.write_bytes(b"old")
            new_pdf.write_bytes(b"new")

            state = deliver_v2(
                {
                    "contract": str(contract_path),
                    "validation": str(validation_path),
                    "hk_variables": str(hk_path),
                    "old_pdf": str(old_pdf),
                    "new_pdf": str(new_pdf),
                },
                "hk",
                "2026-08-27",
                state_path,
            )

        self.assertTrue(state["completed"])
        self.assertFalse(state["old_report_external_delivery"])
        notifier.send_telegram_document.assert_called_once_with(
            str(new_pdf), caption="港股复盘及机会日报｜2026-08-27｜中性"
        )
        email_kwargs = notifier.send_to_email.call_args.kwargs
        self.assertEqual(email_kwargs["attachment_path"], str(new_pdf))
        self.assertNotEqual(email_kwargs["attachment_path"], str(old_pdf))


if __name__ == "__main__":
    unittest.main()
