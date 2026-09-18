from datetime import date
from types import SimpleNamespace
import unittest

from v2.integrated_report import (
    CSS,
    _SourceRegistry,
    _adaptive_chunks,
    _ai_crowding_panel,
    _balanced_chunks,
    _compact_card,
    _component_grid,
    _detail_page,
    _earnings_deep_card,
    _earnings_deep_pages,
    _earnings_temporal_issues,
    _earnings_scenario_block,
    _event_items,
    _event_checkpoint,
    _is_no_catalyst,
    _macro_groups,
    _macro_recent_page,
    _macro_time_display,
    _macro_upcoming_page,
    _macro_probability_meta,
    _market_macro_pages,
    _market_digest,
    _hk_key_variable_page_gaps,
    _hk_flow_sentiment,
    _fallback_market_risks,
    _has_empty_market_risk_panel,
    _hk_market_risks,
    _hk_next_session_focus,
    _hk_pool_coverage,
    _logical_page_count,
    _layout_geometry_issues,
    _plain,
    _public_flat_scenario,
    _public_holding_scenario,
    _public_plan,
    _quality_panel,
    _reader_text,
    _reader_earnings_outlook,
    _required_content_gaps,
    _relative_strength_signal,
    _status_explanation,
    _source_appendix,
    _stale_hk_data_claims,
    _top5_cards,
    _technical_structure_points,
    _top5_validation_page,
    _volume_signal,
    _translated_event_headline,
)


class IntegratedReportTests(unittest.TestCase):
    def test_filing_labels_are_translated_and_events_get_traceable_source_ids(self):
        registry = _SourceRegistry("2026-08-28T16:30:00-04:00")
        event = {
            "event_type": "financial_results",
            "headline": "10-Q filing",
            "effective_at": "2026-07-31T10:01:02Z",
            "published_at": "2026-07-31T10:01:02Z",
            "source": "sec_edgar",
            "source_url": "https://www.sec.gov/example/10q",
        }
        rendered = _event_items([event], registry)
        self.assertEqual(_translated_event_headline("4 filing"), "内部人交易申报（Form 4）")
        self.assertIn("季度报告（10-Q）", rendered)
        self.assertIn("[S1]", rendered)
        appendix = _source_appendix(registry)
        self.assertIn("证据索引与时间口径", appendix)
        self.assertIn("发布日期 2026-07-31", appendix)
        self.assertIn("https://www.sec.gov/example/10q", appendix)

    def test_macro_probability_displays_sample_size_and_calibration_confidence(self):
        high = {
            "probability_sample_size": 141,
            "probability_calibration_level": "historical_category",
        }
        low = {
            "probability_sample_size": 0,
            "probability_calibration_level": "neutral_prior",
        }
        self.assertEqual(_macro_probability_meta(high), ("较高", "同类历史 · 有效样本 n=141 · 置信度较高"))
        self.assertEqual(_macro_probability_meta(low), ("较低", "中性先验 · 无历史有效样本 · 置信度较低"))
        rendered = _macro_upcoming_page(
            [{
                "date": "2026-09-01",
                "title": "制造业 PMI",
                "time": "10:00",
                "time_display": "北京时间 09-01 22:00",
                "forecast": ["预期 50.2"],
                "sensitive_assets": "美债、美元",
                "baseline": "还要观察分项",
                "scenarios": [{
                    "name": "符合预期",
                    "probability_pct": 30,
                    "probability_sample_size": 0,
                    "probability_calibration_level": "neutral_prior",
                    "broad_market": "基准震荡",
                }],
            }]
        )
        self.assertIn("约30%", rendered)
        self.assertIn("无历史有效样本", rendered)

    def test_event_checkpoint_names_event_date_and_gate_expiry(self):
        checkpoint = _event_checkpoint(
            [{
                "event_type": "capital_dilution",
                "effective_at": "2026-08-07T12:00:00Z",
                "gate_until": "2026-09-06",
                "status": "active",
            }]
        )
        self.assertIn("2026-08-07 融资与摊薄", checkpoint)
        self.assertIn("2026-09-06", checkpoint)

    def test_placeholder_catalyst_is_not_treated_as_positive_evidence(self):
        self.assertTrue(_is_no_catalyst("暂无直接利好催化"))
        self.assertFalse(_is_no_catalyst("2026-09-09 发布新一代 iPhone"))

    def test_future_disclosed_earnings_period_is_hidden_and_fails_temporal_audit(self):
        stock = SimpleNamespace(
            symbol="MRVL",
            earnings_outlook=(
                "最近已披露季度 2027Q2（截止 2027-06-30）；"
                "实际EPS 0.94，预期 0.9428；近4季超预期 1 次、低于预期 3 次；"
                "机构推荐（2026-08-01）：强买 13 / 买入 29 / 持有 7 / 卖出 0。"
            ),
        )
        report_day = date(2026, 8, 28)
        issues = _earnings_temporal_issues([stock], report_day)
        self.assertEqual(len(issues), 1)
        self.assertIn("MRVL", issues[0])
        rendered = _reader_earnings_outlook(stock.earnings_outlook, report_day)
        self.assertNotIn("2027-06-30", rendered)
        self.assertNotIn("实际EPS", rendered)
        self.assertIn("机构推荐", rendered)

    def test_disclosed_earnings_period_uses_fiscal_safe_wording(self):
        rendered = _reader_earnings_outlook(
            "最近已披露季度 2026Q2（截止 2026-06-30）；实际EPS 1.91，预期 1.92。",
            date(2026, 8, 28),
        )
        self.assertIn("最近已披露业绩期（期末 2026-06-30）", rendered)
        self.assertNotIn("2026Q2", rendered)

    def test_earnings_scenario_block_renders_three_decision_cases(self):
        item = {
            "earnings_scenario": {
                "status": "upcoming",
                "event_date": "2026-08-26",
                "timing": "盘后",
                "days_to_event": 5,
                "prior": "偏积极",
                "confidence": "中",
                "baseline": "EPS一致预期 2.1283；近4季超预期 4 次",
                "history": {"total": 4, "beats": 4},
                "scenarios": [
                    {"name": "超预期", "decision": "放量站稳224.76后上调成熟度"},
                    {"name": "符合预期", "decision": "观察215.66附近承接"},
                    {"name": "低于预期", "decision": "跌破213.65则原逻辑失效"},
                ],
            }
        }
        stock = SimpleNamespace(earnings_outlook="", sentiment_summary="", hot_topics="")
        rendered = _earnings_scenario_block(item, stock)
        self.assertIn("偏积极", rendered)
        self.assertIn("超预期", rendered)
        self.assertIn("符合预期", rendered)
        self.assertIn("低于预期", rendered)

    def test_detailed_earnings_card_renders_last_result_consensus_and_probabilities(self):
        item = {
            "identity": {"symbol": "NVDA", "name": "英伟达", "market": "us"},
            "earnings_scenario": {
                "status": "upcoming",
                "detailed": True,
                "event_date": "2026-08-26",
                "trading_days_to_event": 3,
                "timing": "盘后",
                "period": "FY2027 Q2",
                "previous_report": {
                    "period": "FY2027 Q1",
                    "metrics": [{"label": "营业收入", "value": "816.15亿美元"}],
                },
                "consensus": {
                    "metrics": [{"label": "EPS一致预期", "value": "2.09美元"}]
                },
                "history": {"total": 4, "beats": 4, "meets": 0, "misses": 0},
                "scenarios": [
                    {"name": "超预期", "probability_pct": 44, "condition": "收入高于930亿美元", "result": "需求确认", "decision": "站稳压力位后上调"},
                    {"name": "符合预期", "probability_pct": 41, "condition": "落在一致预期附近", "result": "高增长兑现", "decision": "观察承接"},
                    {"name": "低于预期", "probability_pct": 15, "condition": "收入低于910亿美元", "result": "预期下修", "decision": "跌破失效位取消"},
                ],
                "key_checks": ["Q3收入指引"],
                "probability_method": "历史兑现与公开预测校准",
                "sources": [{"label": "公司财报", "url": "https://example.com"}],
            },
        }
        rendered = _earnings_deep_card(item)
        self.assertIn("上一次财报", rendered)
        self.assertIn("816.15亿美元", rendered)
        self.assertIn("EPS一致预期", rendered)
        self.assertIn("超预期", rendered)
        self.assertIn("44%", rendered)
        self.assertIn("Q3收入指引", rendered)
        self.assertIn('class="earnings-identity"', rendered)
        self.assertIn('class="earnings-event"', rendered)
        pages = _earnings_deep_pages([item], "美股")
        self.assertEqual(_logical_page_count(pages), 1)

        three_items = [item, item, item]
        self.assertEqual(_logical_page_count(_earnings_deep_pages(three_items, "美股")), 2)
        self.assertEqual(_logical_page_count(_earnings_deep_pages(three_items, "港股")), 2)

    def test_preannounced_earnings_card_is_labelled_as_pre_disclosed(self):
        item = {
            "identity": {"symbol": "HK01300", "name": "俊知集团", "market": "hk"},
            "earnings_scenario": {
                "status": "upcoming",
                "detailed": True,
                "event_kind": "formal_confirmation_after_preannouncement",
                "event_date": "2026-08-27",
                "trading_days_to_event": 1,
                "period": "2026年中期",
                "previous_report": {},
            },
        }
        self.assertIn("核心数据已预披露", _earnings_deep_card(item))

    def test_same_day_earnings_card_says_today_instead_of_dropping_zero(self):
        item = {
            "identity": {"symbol": "HK00883", "name": "中国海洋石油", "market": "hk"},
            "earnings_scenario": {
                "status": "upcoming",
                "detailed": True,
                "event_date": "2026-08-26",
                "trading_days_to_event": 0,
                "period": "2026年中期",
            },
        }
        rendered = _earnings_deep_card(item)
        self.assertIn("<strong>今日</strong>", rendered)
        self.assertNotIn("<strong>个交易日</strong>", rendered)

    def test_plain_removes_internal_explanations_without_dropping_public_text(self):
        text = "数据来源：后台聚合。核心催化仍在，关注放量确认。"
        self.assertEqual(_plain(text), "核心催化仍在，关注放量确认。")

    def test_public_plan_removes_fixed_position_instruction(self):
        text = "建议仓位2成。若放量站稳120，可结合风险预算评估。"
        self.assertEqual(_public_plan(text), "若放量站稳120，可结合风险预算评估。")

    def test_macro_groups_keeps_non_session_dates_and_filters_released_materiality(self):
        payload = {
            "macro_events": [
                {
                    "effective_at": "2026-08-22T09:45:00",
                    "source": "nasdaq_macro_calendar",
                    "source_url": "https://api.nasdaq.com/api/calendar/economicevents?date=2026-08-22",
                    "event_type": "pmi",
                    "headline": "S&P Global PMI Flash",
                    "metadata": {
                        "importance": "high",
                        "category": "pmi",
                        "actual": "56.8",
                        "estimate": "53.9",
                        "previous": "54.6",
                        "analysis": {"status": "released"},
                    },
                },
                {
                    "effective_at": "2026-08-25T11:30:00",
                    "event_type": "rates",
                    "headline": "3-Month Bill Auction",
                    "metadata": {
                        "importance": "medium",
                        "category": "rates",
                        "actual": "3.715%",
                        "estimate": "",
                        "previous": "3.715%",
                        "analysis": {"status": "released"},
                    },
                },
                {
                    "effective_at": "2026-08-26T08:00:00",
                    "event_type": "housing",
                    "headline": "Building Permits",
                    "metadata": {
                        "importance": "high",
                        "category": "housing",
                        "analysis": {"status": "scheduled"},
                        "estimate": "1.4M",
                        "previous": "1.3M",
                        "scenarios": [
                            {"name": "高于预期", "probability_pct": 30},
                            {"name": "大致符合预期", "probability_pct": 40},
                            {"name": "低于预期", "probability_pct": 30},
                        ],
                    },
                },
                {
                    "effective_at": "2026-08-26T10:30:00",
                    "event_type": "regional_survey",
                    "headline": "Dallas Fed Manufacturing Index",
                    "metadata": {
                        "importance": "medium",
                        "category": "survey",
                        "analysis": {"status": "scheduled"},
                        "estimate": "1.0",
                        "previous": "0.5",
                    },
                },
            ]
        }
        recent, upcoming = _macro_groups(payload, date(2026, 8, 25))
        self.assertEqual([item["date"] for item in recent], ["2026-08-21"])
        self.assertGreater(recent[0]["surprise_pct"], 5)
        self.assertNotIn("Bill Auction", recent[0]["title"])
        self.assertEqual([item["date"] for item in upcoming], ["2026-08-26"])
        self.assertEqual(len(upcoming[0]["scenarios"]), 3)
        self.assertIn("符合预期", upcoming[0]["scenarios"][1]["name"])
        self.assertIn("绝对水平", upcoming[0]["baseline"])
        self.assertIn("北京时间", upcoming[0]["time_display"])
        self.assertIn("REITs", upcoming[0]["sensitive_assets"])

    def test_macro_groups_analyzes_high_ppi_yoy_without_losing_mom_context(self):
        payload = {
            "macro_events": [
                {
                    "effective_at": "2026-09-10T12:30:00+00:00",
                    "event_type": "ppi",
                    "headline": title,
                    "metadata": {
                        "importance": "high",
                        "category": "inflation",
                        "actual": actual,
                        "estimate": estimate,
                        "previous": previous,
                        "unit": unit,
                        "analysis": {"status": "released", "comparison": comparison},
                    },
                }
                for title, actual, estimate, previous, unit, comparison in (
                    ("PPI (MoM)", "0.4%", "0.4%", "0.1%", "MoM", "inline"),
                    ("PPI (YoY)", "5.4%", "5.3%", "4.8%", "YoY", "above"),
                    ("Core PPI (MoM)", "0.2%", "0.3%", "0.3%", "MoM", "below"),
                    ("Core PPI (YoY)", "4.6%", "4.6%", "4.3%", "YoY", "inline"),
                )
            ]
        }

        recent, _upcoming = _macro_groups(payload, date(2026, 9, 10))

        self.assertEqual(len(recent), 1)
        self.assertIn("PPI (YoY)：实际 5.4%｜预期 5.3%｜前值 4.8%", recent[0]["released_values"])
        self.assertIn("Core PPI (MoM)：实际 0.2%｜预期 0.3%｜前值 0.3%", recent[0]["released_values"])
        self.assertIn("均较前值回升", recent[0]["summary"])
        self.assertIn("不能因单一环比分项偏弱", recent[0]["summary"])
        self.assertIn("降息预期可能后移", recent[0]["market"])
        self.assertIn("高估值科技", recent[0]["negative"])

    def test_macro_groups_keeps_only_two_highest_priority_released_groups(self):
        payload = {"macro_events": []}
        for day, actual in (
            ("2026-08-21", "101"),
            ("2026-08-22", "102"),
            ("2026-08-23", "110"),
        ):
            payload["macro_events"].append(
                {
                    "effective_at": f"{day}T12:30:00+00:00",
                    "event_type": "growth",
                    "headline": f"Growth {day}",
                    "metadata": {
                        "importance": "high",
                        "category": "growth",
                        "actual": actual,
                        "estimate": "100",
                        "previous": "99",
                        "analysis": {"status": "released"},
                    },
                }
            )
        recent, _upcoming = _macro_groups(payload, date(2026, 8, 25))
        self.assertEqual(len(recent), 2)
        self.assertEqual([item["date"] for item in recent], ["2026-08-22", "2026-08-23"])

    def test_macro_card_keeps_time_consensus_and_market_scenarios(self):
        rendered = _macro_upcoming_page(
            [
                {
                    "date": "2026-08-26",
                    "title": "建筑许可",
                    "time": "08:00 GMT",
                    "time_display": _macro_time_display("2026-08-26T08:00:00+00:00"),
                    "forecast": ["建筑许可：预期 1.4M｜前值 1.3M"],
                    "sensitive_assets": "美债、REITs、金融与地产链",
                    "baseline": "关注绝对水平。",
                    "scenarios": [
                        {
                            "name": "高于预期",
                            "probability_pct": 30,
                            "condition": "高于 1.4M",
                            "broad_market": "周期预期改善",
                            "positive_sectors": ["金融"],
                            "negative_sectors": ["REITs"],
                        },
                        {
                            "name": "温和增长 / 软着陆",
                            "probability_pct": 40,
                            "broad_market": "原有市场主线延续",
                            "positive_sectors": [],
                            "negative_sectors": [],
                        }
                    ],
                }
            ]
        )
        self.assertIn("北京时间", rendered)
        self.assertIn("市场预期 / 前值", rendered)
        self.assertIn("周期预期改善", rendered)
        self.assertIn("原有市场主线延续；还要看：关注绝对水平", rendered)
        self.assertNotIn("符合预期也要看", rendered)
        self.assertNotIn("触发阈值", rendered)

    def test_us_macro_pages_are_not_reused_in_hk_report(self):
        self.assertEqual(_market_macro_pages("hk", [], []), "")
        rendered = _market_macro_pages("us", [], [])
        self.assertEqual(rendered, "")
        rendered = _market_macro_pages(
            "us",
            [
                {
                    "date": "2026-08-22",
                    "title": "PMI",
                    "time": "",
                    "released_values": [],
                    "summary": "",
                    "market": "",
                    "positive": [],
                    "negative": [],
                    "surprise_pct": 5.4,
                }
            ],
            [
                {
                    "date": "2026-08-26",
                    "title": "核心 PCE",
                    "time": "",
                    "forecast": ["核心 PCE：预期 0.2%｜前值 0.1%"],
                    "sensitive_assets": "美债、美元、高估值成长股",
                    "baseline": "关注核心与服务分项。",
                    "scenarios": [],
                }
            ],
        )
        self.assertIn('id="macro-combined"', rendered)
        self.assertIn('class="macro-stack cards-1"', rendered)
        self.assertIn("最近七日", rendered)
        self.assertIn("未来七日高影响宏观情景", rendered)

    def test_hk_focus_uses_available_breadth_volume_and_southbound(self):
        ranked = [
            {
                "market_data": {
                    "change_pct": change,
                    "volume": {"confirmation": {"value": volume}},
                }
            }
            for change, volume in [(2, 1.2), (1, 0.8), (-1, 1.1), (0, 0.9)]
        ]
        coverage = _hk_pool_coverage(ranked)
        self.assertEqual(coverage["total"], 4)
        self.assertEqual(coverage["advancers"], 2)
        self.assertEqual(coverage["decliners"], 1)
        self.assertEqual(coverage["volume_confirmed"], 2)
        self.assertEqual(coverage["volume_weak"], 0)
        self.assertEqual(coverage["relative_strength_positive"], 0)
        focus = _hk_next_session_focus(ranked, {"southbound": {"net_hkd_bn": -7.63}})
        self.assertIn("2只上涨、1只平盘、1只下跌", focus)
        self.assertIn("2只股票的量比达到1.05倍以上", focus)
        self.assertIn("南向资金当日净卖出76.3亿港元", focus)
        self.assertIn("不追高", focus)
        self.assertNotIn("缺少", focus)

        flow = _hk_flow_sentiment(ranked, {"southbound": {"net_hkd_bn": -7.63}})
        self.assertIn("4只股票中，2只上涨、1只平盘、1只下跌", flow)
        self.assertIn("观察池中位量比为1.00倍", flow)
        self.assertIn("南向资金当日净卖出76.3亿港元", flow)
        self.assertIn("成交量确认", flow)
        self.assertNotIn("缺少", flow)

    def test_stale_hk_data_claim_audit_targets_superseded_market_copy(self):
        document = "<p>指数上涨，但缺少量能、广度与南向数据验证。</p><p>财务/估值依据不足。</p>"
        claims = _stale_hk_data_claims(document)
        self.assertEqual(len(claims), 1)
        self.assertIn("南向", claims[0])
        self.assertNotIn("财务", claims[0])

    def test_hk_market_risks_replace_stale_claim_with_merged_available_facts(self):
        ranked = [
            {
                "market_data": {
                    "change_pct": change,
                    "volume": {"confirmation": {"value": volume}},
                }
            }
            for change, volume in [(2, 1.2), (1, 0.8), (-1, 1.1), (0, 0.9)]
        ]
        risks = _hk_market_risks(
            [
                "今日缺乏南向资金、涨跌家数等关键数据，市场全貌判断存在不确定性。",
                "全球市场波动可能影响港股风险偏好。",
            ],
            ranked,
            {"southbound": {"net_hkd_bn": 2.14}},
        )

        self.assertIn("2只上涨、1只平盘、1只下跌", risks[0])
        self.assertIn("南向资金当日净买入21.4亿港元", risks[0])
        self.assertIn("全球市场波动可能影响港股风险偏好。", risks)
        self.assertEqual(_stale_hk_data_claims("".join(risks)), [])

    def test_market_risk_fallback_is_grounded_in_pool_breadth_and_events(self):
        ranked = [
            {
                "market_data": {
                    "change_pct": change,
                    "volume": {"confirmation": {"value": volume}},
                }
            }
            for change, volume in [(2, 1.2), (1, 0.8), (-1, 1.1), (0, 0.9)]
        ]
        risks = _fallback_market_risks("us", ranked, 3)
        self.assertEqual(len(risks), 3)
        self.assertIn("2只上涨、1只平盘、1只下跌", risks[0])
        self.assertIn("2只股票的量比达到1.05倍以上", risks[1])
        self.assertIn("3只股票处于事件或风险观察状态", risks[2])

    def test_empty_market_risk_panel_is_a_content_failure(self):
        empty = '<div class="panel"><h3>主要市场风险</h3><ul class="reader-bullets"></ul></div>'
        filled = '<div class="panel"><h3>主要市场风险</h3><ul><li>量能尚未扩散。</li></ul></div>'
        self.assertTrue(_has_empty_market_risk_panel(empty))
        self.assertFalse(_has_empty_market_risk_panel(filled))

    def test_hk_key_variable_page_audit_catches_clipped_tail(self):
        clipped = ["港股关键变量 观察池广度与量能分布 定价拆解与次日验证 量能扩散 南向流向"]
        gaps = _hk_key_variable_page_gaps(clipped)
        self.assertIn("外部映射", gaps)
        self.assertIn("汇率边界", gaps)
        self.assertIn("下一交易日重点", gaps)
        complete = clipped[0] + " 外部映射 汇率边界 下一交易日重点"
        self.assertEqual(_hk_key_variable_page_gaps([complete]), [])

    def test_released_macro_card_keeps_actual_consensus_and_impact(self):
        rendered = _macro_recent_page(
            [
                {
                    "date": "2026-08-20",
                    "title": "每周失业救济",
                    "time": "08:30 GMT",
                    "time_display": "北京时间 08-20 20:30｜美东 08-20 08:30",
                    "released_values": ["初请失业金：实际 240K｜预期 235K｜前值 232K"],
                    "summary": "实际值高于预期。",
                    "market": "就业边际转弱，利率压力缓和。",
                    "positive": ["小盘股"],
                    "negative": ["美元"],
                    "surprise_pct": 2.1,
                }
            ]
        )
        self.assertIn("实际 / 预期 / 前值", rendered)
        self.assertIn("240K", rendered)
        self.assertIn("对市场", rendered)
        self.assertIn("最大预期偏差", rendered)

    def test_required_content_check_rejects_blank_market_sections(self):
        formal = SimpleNamespace(market_status="", main_theme="", strategy="")
        research = SimpleNamespace(
            one_sentence="", structure="", sectors="", flow_sentiment="", catalysts=""
        )
        gaps = _required_content_gaps(formal, research)
        self.assertIn("主线方向", gaps)
        self.assertIn("指数结构", gaps)

    def test_reader_text_drops_missing_data_commentary(self):
        text = "由于系统未提供成交额，以下判断基于有限信息推断。科技主线仍在，但需观察量能。"
        self.assertEqual(_reader_text(text), "科技主线仍在，但需观察量能。")

    def test_balanced_chunks_avoid_two_item_last_page(self):
        chunks = _balanced_chunks(list(range(14)), 4)
        self.assertEqual([len(chunk) for chunk in chunks], [4, 4, 3, 3])

    def test_adaptive_chunks_uses_two_cards_when_copy_is_heavy(self):
        chunks = _adaptive_chunks(list(range(8)), lambda _: 23)
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 2, 2])

    def test_adaptive_chunks_keeps_four_cards_when_copy_is_short(self):
        chunks = _adaptive_chunks(list(range(8)), lambda _: 12)
        self.assertEqual([len(chunk) for chunk in chunks], [4, 4])

    def test_adaptive_chunks_rebalances_single_card_tail(self):
        chunks = _adaptive_chunks(list(range(10)), lambda _: 23)
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 2, 2, 2])

    def test_adaptive_chunks_keeps_single_rich_card_instead_of_overflowing(self):
        chunks = _adaptive_chunks(list(range(7)), lambda _: 23)
        self.assertEqual([len(chunk) for chunk in chunks], [2, 2, 2, 1])

    def test_event_observation_distinguishes_upcoming_from_recent(self):
        upcoming = _status_explanation("事件观察", [{"status": "upcoming"}])
        recent = _status_explanation("事件观察", [{"status": "released"}])
        self.assertIn("尚未落地", upcoming)
        self.assertIn("刚发生", recent)

    def test_market_digest_does_not_leave_enumeration_intro_as_a_bullet(self):
        rendered = _market_digest("影响明日交易的催化因素有三层：美债利率。零售财报。存储芯片景气。")
        self.assertNotIn("影响明日交易的催化因素有三层", rendered)
        self.assertNotIn("<li>", rendered)
        self.assertNotIn("digest-row", rendered)
        self.assertNotIn("关键信号", rendered)
        self.assertIn("美债利率", rendered)
        self.assertIn("零售财报", rendered)
        self.assertIn("存储芯片景气", rendered)

    def test_market_digest_keeps_single_coherent_sentence_as_prose(self):
        rendered = _market_digest("资金从高估值成长流向低估值价值，防御属性增强。")
        self.assertIn("market-copy", rendered)
        self.assertNotIn("digest-row", rendered)

    def test_hk_market_page_uses_whitespace_for_readable_complete_prose(self):
        rendered = _market_digest(
            "恒生科技指数弹性高于恒生指数，但国企指数涨幅更大，说明市场由科技成长与中资权重共同推动。",
            max_points=5,
            limit_each=220,
        )
        self.assertIn("科技成长与中资权重共同推动", rendered)
        self.assertNotIn("…", rendered)
        self.assertIn(".hk-market-page .market-copy,.hk-market-page .reader-bullets{font-size:11.2px", CSS)

    def test_ai_crowding_panel_uses_gauge_and_pointer_bars(self):
        rendered = _ai_crowding_panel(
            {
                "crowding_index": 67,
                "break_risk": 63,
                "momentum_heat": 67,
                "speculation_heat": 68,
                "breadth_health": 32,
                "status": "拥挤升温",
                "interpretation": "趋势仍强，但追涨性价比下降。",
                "risk_lines": ["QQQ 跌破关键位时风险偏好转弱。"],
            }
        )
        self.assertIn("crowding-gauge", rendered)
        self.assertIn("pointer-track", rendered)
        self.assertIn("left:67%", rendered)
        self.assertNotIn("🔥", rendered)

    def test_ai_trend_percentile_is_not_rendered_as_a_signed_change(self):
        rendered = _ai_crowding_panel(
            {
                "crowding_index": 67,
                "break_risk": 63,
                "momentum_heat": 67,
                "speculation_heat": 68,
                "breadth_health": 32,
                "status": "拥挤升温",
                "interpretation": "趋势仍强。",
                "change_1d": 4,
                "change_5d": -2,
                "percentile_1y": 84,
                "history_samples": 20,
            }
        )
        self.assertIn("+4", rendered)
        self.assertIn("-2", rendered)
        self.assertIn(">84%</b>", rendered)
        self.assertNotIn(">+84%</b>", rendered)

    def test_reader_facing_copy_uses_readable_font_floor(self):
        self.assertIn("body{font-size:9.6px", CSS)
        self.assertIn("small{font-size:8.2px", CSS)
        self.assertIn(".ai-dashboard-copy>p{font-size:9.6px", CSS)
        self.assertIn(".ai-risk-tags span{font-size:8.3px", CSS)

    def test_reader_facing_copy_replaces_stale_company_name(self):
        self.assertEqual(_plain("华虹半导体财报"), "华虹宏力财报")
        self.assertEqual(_plain("Alibaba与Shein消息"), "阿里巴巴与希音（SHEIN）消息")

    def test_market_digest_removes_internal_data_limit_and_irrelevant_news_notes(self):
        rendered = _market_digest(
            "由于缺乏板块明细数据，仅能结合指数特征推断：国企指数领涨。"
            "此外，新闻中大量A股复盘内容与港股无直接关联，不应作为港股判断依据。"
        )
        self.assertIn("从指数相对表现看", rendered)
        self.assertIn("国企指数领涨", rendered)
        self.assertNotIn("缺乏板块明细数据", rendered)
        self.assertNotIn("A股复盘", rendered)

    def test_earnings_cards_use_natural_flow_without_flex_spacing(self):
        self.assertIn(".earnings-deep-stack{display:grid;gap:2.2mm;height:auto", CSS)
        self.assertIn(".earnings-deep-stack.cards-3,.earnings-deep-stack.cards-2,.earnings-deep-stack.cards-1{grid-template-rows:none}", CSS)
        self.assertIn(".earnings-deep-stack.cards-2{height:auto;grid-template-rows:none;align-content:start", CSS)
        self.assertIn(".earnings-deep-stack.cards-2 .earnings-deep-card{display:block;height:auto", CSS)
        self.assertNotIn("justify-content:space-between;height:auto;padding:4mm", CSS)
        self.assertNotIn(".earnings-deep-stack{display:grid;gap:2.2mm;height:247mm", CSS)

    def test_earnings_header_reserves_non_overlapping_identity_and_event_columns(self):
        self.assertIn(
            ".earnings-deep-head{display:grid;grid-template-columns:minmax(0,1fr) minmax(43mm,auto)",
            CSS,
        )
        self.assertIn(".earnings-identity,.earnings-event{min-width:0}", CSS)
        self.assertIn(".earnings-event{max-width:74mm;text-align:right}", CSS)

    def test_top5_validation_page_keeps_only_latest_and_rolling_summary(self):
        summary = {
            "markets": {
                "us": {
                    "through_date": "2026-08-25",
                    "latest_period": {
                        "date": "2026-08-21",
                        "items": [
                            {
                                "rank": 1,
                                "symbol": "PLTR",
                                "name": "PLTR",
                                "return_1d": -2.25,
                                "return_5d": None,
                                "return_20d": None,
                                "mfe": 1.39,
                                "mae": -4.8,
                            }
                        ],
                    },
                    "rolling_20": {
                        "periods": 20,
                        "win_rate_1d": 48.4,
                        "average_return_1d": 0.97,
                        "win_rate_5d": 56,
                        "average_return_5d": 4.2,
                        "average_mfe": 13.39,
                        "average_mae": -6.65,
                    },
                    "shadow_rolling_20": {"periods": 2, "triggered": 1, "trigger_eligible": 2},
                    "comparison": {"average_overlap": 50},
                    "by_score_band": [{"group": "70–79", "signals": 4, "win_rate_1d": 50, "average_return_1d": 1.2}],
                    "by_signal_type": [{"group": "等待触发", "signals": 3, "win_rate_1d": 33.3, "average_return_1d": -0.8}],
                    "weight_validation": {"decision": "样本不足，维持35/25/20/15/5，不调整权重"},
                    "latest_shadow_run": {"pool_size": 20, "official_coverage": 100, "relative_strength_coverage": 100, "volume_coverage": 100, "overlap_ratio": 60},
                }
            }
        }
        ranked = [{"identity": {"symbol": "PLTR", "name": "Palantir"}}]
        rendered = _top5_validation_page(summary, "us", ranked)
        self.assertIn("最近完成期", rendered)
        self.assertIn("滚动20期", rendered)
        self.assertIn("Palantir", rendered)
        self.assertIn("维持35/25/20/15/5", rendered)
        self.assertNotIn("V2", rendered)
        self.assertNotIn("大段方法", rendered)

    def test_pdf_grid_spans_avoid_weasyprint_negative_line_bug(self):
        self.assertNotIn("grid-column:1/-1", CSS)
        self.assertIn(".technical-weight-note{grid-column:1 / span 6", CSS)
        self.assertIn(".ai-crowding-panel{grid-column:1 / span 2", CSS)

    def test_status_legend_keeps_each_explanation_on_one_line(self):
        self.assertIn(".status-legend{display:flex;flex-wrap:nowrap", CSS)
        self.assertIn(".status-legend>span{display:flex;flex:none", CSS)
        self.assertIn("white-space:nowrap", CSS)

    def test_geometry_audit_accepts_a_well_formed_component_grid(self):
        from weasyprint import HTML

        components = "".join("<div class='component'>评分</div>" for _ in range(6))
        document = HTML(
            string=(
                "<style>"
                ".component-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:4px}"
                ".technical-weight-note{grid-column:1 / span 6;display:flex}"
                "</style>"
                f"<div class='component-grid'>{components}"
                "<div class='technical-weight-note'>技术分明细</div></div>"
            )
        ).render()
        self.assertEqual(_layout_geometry_issues(document), [])

    def test_geometry_audit_rejects_overlapping_earnings_card_sections(self):
        from weasyprint import HTML

        document = HTML(
            string=(
                "<style>"
                ".earnings-deep-card{position:relative;height:100px}"
                ".first{height:80px}"
                ".second{position:absolute;top:50px;height:20px}"
                "</style>"
                "<article class='earnings-deep-card'>"
                "<div class='first'>上一区块</div><div class='second'>重叠区块</div>"
                "</article>"
            )
        ).render()
        issues = _layout_geometry_issues(document)
        self.assertTrue(any("earnings-deep-card 内部内容重叠" in issue for issue in issues))

    def test_observation_cards_are_not_clipped(self):
        self.assertIn(".compact-card{overflow:visible;break-inside:avoid-page}", CSS)
        self.assertIn(".compact-stack{height:auto;min-height:250mm", CSS)

    def test_logical_page_count_detects_page_sections(self):
        document = '<section class="page"></section><section class="page stock-detail"></section>'
        self.assertEqual(_logical_page_count(document), 2)

    def test_ranking_signals_summarize_detail_columns(self):
        self.assertEqual(
            _relative_strength_signal({"market_excess_20d_pct": 5, "industry_excess_20d_pct": 4}),
            "双重领先",
        )
        self.assertEqual(_volume_signal({"value": 1.4}), ("明显放量", "1.40×"))

    def test_top5_cards_put_checkpoint_in_decision_block(self):
        item = {
            "identity": {"name": "测试公司", "symbol": "TEST"},
            "opportunity": {
                "rank": 1,
                "score": 82,
                "primary_catalyst": "订单改善",
                "primary_risk": "需求波动",
            },
            "execution": {
                "status": "等待触发",
                "note": "等待放量突破",
                "standard_trade_card": {
                    "trigger_condition": "旧条件",
                    "watch_condition": "收盘突破前高并获得量能确认",
                    "setup_label": "平台/前高突破",
                },
            },
        }
        rendered = _top5_cards([item], {})
        self.assertIn("top5-decision", rendered)
        self.assertIn("下一确认点", rendered)
        self.assertIn("收盘突破前高", rendered)
        self.assertNotIn("旧条件", rendered)
        self.assertNotIn("<table", rendered)

    def test_technical_section_uses_price_structure_not_ma_narrative(self):
        stock = SimpleNamespace(
            technical={
                "ma_alignment": "MA5 > MA10 > MA20，多头排列",
                "trend_score": 90,
                "support_level": 95,
                "resistance_level": 110,
            },
            volume_meaning="温和放量，价格承接稳定",
        )
        item = {
            "market_data": {
                "price_structure": {
                    "status": "ok",
                    "trend": "higher_high_higher_low",
                    "breakout_state": "inside_range",
                    "breakout_level": 108,
                    "retest_level": 101,
                    "last_swing_low": 97,
                    "next_resistance": 112,
                    "range_position_pct": 72,
                },
                "volume": {"confirmation": {"value": 1.2}},
            },
            "relative_strength": {
                "stock": {"return_5d_pct": 3, "return_20d_pct": 8},
                "market_excess_5d_pct": 2,
                "market_excess_20d_pct": 4,
                "industry_excess_5d_pct": 1,
                "industry_excess_20d_pct": 3,
            },
            "execution": {"standard_trade_card": {}},
        }
        rendered = " ".join(value for _, value in _technical_structure_points(item, stock))
        self.assertIn("高点与低点同步抬高", rendered)
        self.assertIn("平台上沿 108", rendered)
        self.assertIn("双重领先", rendered)
        self.assertNotIn("MA5", rendered)

    def test_score_grid_exposes_structured_technical_weights(self):
        item = {
            "opportunity": {
                "technical_score": 78,
                "technical_components": {
                    "price_structure": 30,
                    "relative_strength_sector": 20,
                    "volume_confirmation": 15,
                    "volatility_risk": 10,
                    "auxiliary_indicators": 3,
                },
                "components": {
                    "technical_composite": 35.1,
                    "catalyst_expectation": 15,
                    "fundamental_valuation": 10,
                    "event_news_value": 8,
                    "sentiment_crowding": 4,
                    "data_trust": 5,
                },
            }
        }
        rendered = _component_grid(item)
        self.assertIn("结构化技术分", rendered)
        self.assertIn("技术分 78", rendered)
        self.assertIn("价格结构 30", rendered)
        self.assertIn("辅助指标 3", rendered)

    def test_top5_structure_events_and_boundaries_share_one_page(self):
        stock = SimpleNamespace(
            risks=["需求波动"],
            catalysts=["订单改善"],
            social_lines=[],
            conclusion="趋势改善",
            current_price="10.00 港元",
            change_pct="1.00%",
            technical={},
            volume_meaning="量能平稳",
            earnings_outlook="业绩稳定",
            sentiment_summary="情绪中性",
            hot_topics="行业景气",
        )
        item = {
            "identity": {"name": "测试公司", "symbol": "HK00001"},
            "opportunity": {
                "rank": 1,
                "score": 80,
                "primary_catalyst": "订单改善",
                "technical_score": 78,
                "technical_components": {
                    "price_structure": 30,
                    "relative_strength_sector": 20,
                    "volume_confirmation": 15,
                    "volatility_risk": 10,
                    "auxiliary_indicators": 3,
                },
                "components": {
                    "technical_composite": 35.1,
                    "catalyst_expectation": 15,
                    "fundamental_valuation": 10,
                    "event_news_value": 8,
                    "sentiment_crowding": 4,
                    "data_trust": 5,
                },
            },
            "execution": {
                "status": "等待触发",
                "note": "等待确认",
                "standard_trade_card": {},
            },
            "quality": {"data_completeness": 90, "signal_confidence": 75, "major_gaps": []},
            "relative_strength": {
                "stock": {"return_5d_pct": 2, "return_20d_pct": 5},
                "market_excess_5d_pct": 1,
                "market_excess_20d_pct": 2,
                "industry_excess_5d_pct": 1,
                "industry_excess_20d_pct": 2,
            },
            "market_data": {
                "current_price": 10,
                "change_pct": 1,
                "atr14": 0.4,
                "price_structure": {},
                "volume": {"confirmation": {"value": 1.1}, "turnover_rate": 1.2},
            },
        }
        rendered = _detail_page(item, stock, [], "港股")
        self.assertEqual(_logical_page_count(rendered), 1)
        self.assertIn("价格结构与确认", rendered)
        self.assertIn("关键公司事件", rendered)
        self.assertIn("条件与风险边界", rendered)
        self.assertNotIn("stock-continuation", rendered)

    def test_public_scenarios_ignore_legacy_ma_instructions(self):
        item = {
            "execution": {
                "status": "等待触发",
                "standard_trade_card": {
                    "watch_condition": "回踩 101 结构位不破并收盘转强",
                    "invalidation_condition": "收盘跌破 97，原回踩逻辑失效",
                },
            }
        }
        flat = _public_flat_scenario(item, [])
        holding = _public_holding_scenario(item, [])
        self.assertIn("回踩 101", flat)
        self.assertIn("收盘跌破 97", holding)
        self.assertNotIn("MA", flat + holding)

    def test_cautious_execution_has_distinct_reader_status(self):
        self.assertIn("软约束", _status_explanation("谨慎可执行"))
        self.assertIn(".status-cautious", CSS)

    def test_public_flat_scenario_does_not_call_confirmed_breakout_unconfirmed(self):
        item = {
            "execution": {
                "status": "可执行",
                "standard_trade_card": {
                    "price_condition_met": True,
                    "watch_condition": "已突破 105；下一交易日观察能否守稳",
                },
            }
        }
        flat = _public_flat_scenario(item, [])
        self.assertIn("空仓者不追价", flat)
        self.assertNotIn("条件未确认", flat)

    def test_quality_panel_labels_level_and_missing_evidence(self):
        rendered = _quality_panel(
            {"data_completeness": 92, "signal_confidence": 41, "major_gaps": ["财务/估值依据不足"]}
        )
        self.assertIn("数据完整度", rendered)
        self.assertIn("信号置信度", rendered)
        self.assertIn("<em>高</em>", rendered)
        self.assertIn("<em>低</em>", rendered)
        self.assertIn("quality-track", rendered)
        self.assertIn("财务/估值依据不足", rendered)

    def test_observation_card_keeps_basic_quote_and_trading_fields(self):
        stock = SimpleNamespace(
            open_price="10.00 美元",
            high_price="10.80 美元",
            low_price="9.80 美元",
            current_price="10.50 美元",
            change_pct="5.00%",
            amplitude="10.00%",
            volume="1200.00 万股",
            amount="1.26 亿美元",
            turnover_rate="2.30%",
            conclusion="趋势改善",
            catalysts=["订单改善"],
            latest_news="",
            risks=["需求波动"],
            risk_control="跌破支撑重估",
            flat_advice="等待确认",
            entry_plan="",
            holding_advice="持有观察",
        )
        item = {
            "identity": {"name": "测试公司", "symbol": "TEST"},
            "opportunity": {"rank": 6, "score": 68, "primary_catalyst": "订单改善"},
            "execution": {"status": "等待触发", "note": "等待放量"},
            "relative_strength": {
                "stock": {"return_5d_pct": 3, "return_20d_pct": 8},
                "market_excess_5d_pct": 2,
                "market_excess_20d_pct": 4,
                "industry_excess_5d_pct": 1,
                "industry_excess_20d_pct": 3,
            },
            "market_data": {
                "current_price": 10.5,
                "change_pct": 5,
                "volume": {"confirmation": {"value": 1.4}},
            },
        }
        rendered = _compact_card(item, stock)
        for label in ("开盘", "最高", "最低", "收盘", "日涨跌", "成交量", "成交额", "换手率"):
            self.assertIn(label, rendered)
        self.assertIn("10.00 美元", rendered)
        self.assertIn("1.40×", rendered)


if __name__ == "__main__":
    unittest.main()
