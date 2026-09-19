import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from reporting.dedup_report import DedupReport
from reporting.research_report import load_stocks, parse_market_research, render_html


class ResearchReportTest(unittest.TestCase):
    def test_market_research_keeps_sector_and_ai_crowding(self):
        source = """# 🎯 大盘复盘
> 一句话：指数上涨但内部结构分化。
### 一、盘面总览
市场整体定性为“震荡偏暖、结构分化”。
### 二、指数结构
关键指数站稳支撑。
| 指数 | 最新 | 涨跌幅 | 振幅 |
|---|---:|---:|---:|
| 标普500 | 7000 | +1% | 2% |
### 三、板块主线
网页证据显示存储芯片领涨，软件承压。
### 四、资金与情绪
资金转向价值蓝筹。
### 五、消息催化
美债利率是核心变量。
### 六、明日交易计划
- 保持均衡仓位。
### 六、AI拥挤度监控
- 热度 47/100
- QQQ 跌破关键位时降风险。
### 七、风险提示
1. 利率继续上行。
# 🎯 2026-08-21 决策仪表盘
"""
        result = parse_market_research(source)
        self.assertEqual(result.regime, "震荡偏暖、结构分化")
        self.assertIn("存储芯片", result.sectors)
        self.assertEqual(len(result.index_rows), 1)
        self.assertEqual(len(result.ai_crowding), 2)

    def test_structured_stock_keeps_relative_volume_social_and_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "test.db"
            con = sqlite3.connect(db)
            con.executescript(
                """
                CREATE TABLE analysis_history (
                  id INTEGER PRIMARY KEY, code TEXT, name TEXT, report_type TEXT,
                  sentiment_score INTEGER, operation_advice TEXT, trend_prediction TEXT,
                  raw_result TEXT, created_at TEXT
                );
                CREATE TABLE stock_daily (
                  code TEXT, date TEXT, volume_ratio REAL, data_source TEXT
                );
                """
            )
            raw = {
                "sentiment_score": 80,
                "trend_prediction": "看多",
                "confidence_level": "高",
                "market_snapshot": {"close": "100 美元", "pct_chg": "1%", "turnover_rate": "2%", "source": "longbridge"},
                "dashboard": {
                    "core_conclusion": {"one_sentence": "趋势健康", "position_advice": {"no_position": "等待触发", "has_position": "继续持有"}},
                    "data_perspective": {"trend_status": {"trend_score": 90}, "price_position": {"ma5": 99}, "volume_analysis": {"volume_meaning": "量价健康"}},
                    "intelligence": {"risk_alerts": ["风险A"], "positive_catalysts": ["催化A"], "earnings_outlook": "财报完整", "social_sentiment_summary": ["Reddit：热度 70/100"]},
                    "battle_plan": {"sniper_points": {"ideal_buy": "99", "stop_loss": "95", "take_profit": "110"}, "position_strategy": {"suggested_position": "2成"}},
                },
            }
            con.execute(
                "INSERT INTO analysis_history VALUES (1,'MU','美光','full',80,'买入','看多',?,?)",
                (json.dumps(raw, ensure_ascii=False), "2026-08-21 20:00:00"),
            )
            con.execute("INSERT INTO stock_daily VALUES ('MU','2026-08-21',0.69,'LongbridgeFetcher')")
            con.commit()
            con.close()
            review = root / "review.json"
            review.write_text(
                json.dumps({"decision_quality": {"MU": {"market": "us", "final_action": "可执行计划"}}, "trading_cards": {"MU": {"risk_reward": 1.8}}}),
                encoding="utf-8",
            )
            stocks = load_stocks(db, "2026-08-21", "us", review)
            self.assertEqual(len(stocks), 1)
            self.assertEqual(stocks[0].relative_volume, 0.69)
            self.assertIn("Reddit", stocks[0].social_lines[0])
            self.assertEqual(stocks[0].review["final_action"], "可执行计划")

            formal = DedupReport("2026-08-21", "us", "分化", "AI", "轻仓", [], [], [], "fixture")
            document = render_html(formal, parse_market_research(""), stocks)
            self.assertIn("相对成交量", document)
            self.assertIn("美股社媒舆情", document)
            self.assertIn("质量、事件与执行复核", document)


if __name__ == "__main__":
    unittest.main()
