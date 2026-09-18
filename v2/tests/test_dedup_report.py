from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from v2.dedup_report import parse_report, render_html


SAMPLE = """# 今日策略结论

市场状态：震荡偏强
当前主线：半导体
今日最值得关注：
* 示例公司
今日不建议重仓参与：
* 风险公司
今日策略：
只做确认后的机会。

# 附录：原始详细分析
# 🎯 2026-08-21 决策仪表盘

## 🟢 示例公司 (TEST)
### 📰 重要信息速览
**🚨 风险警报**:
- 第一项风险
**✨ 利好催化**:
- 第一项催化
**📢 最新动态**: 无
### 📌 核心结论
**🟢 买入** | 看多
> **一句话决策**: 站稳支撑后执行
⏰ **时效性**: 今日内
| 持仓情况 | 操作建议 |
|---|---|
| 🆕 **空仓者** | 等待站稳 10 元后介入。不要追高。 |
| 💼 **持仓者** | 跌破 9 元减仓。 |
### 📈 当日行情
| 收盘 | 昨收 | 开盘 | 最高 | 最低 | 涨跌幅 | 涨跌额 | 振幅 | 成交量 | 成交额 |
|---|---|---|---|---|---|---|---|---|---|
| 10.00 元 | 9.80 元 | 9.90 元 | 10.20 元 | 9.70 元 | 2.04% | 0.20 元 | 5.1% | 100 万股 | 1000 万元 |
| 当前价 | 量比 | 换手率 | 行情来源 |
|---|---|---|---|
| 10.00 元 | N/A | 1.20% | 长桥 |
### 🎯 作战计划
| 操作点位 | 当前价（元） |
|---|---|
| 🎯 理想买入点 | 10.00 元 |
| 🔵 次优买入点 | 9.80 元 |
| 🛑 止损位 | 9.00 元 |
| 🎊 目标位 | 12.00 元 |
**💰 仓位建议**: 2 成
**✅ 检查清单**
- ✅ 检查项1：趋势
- ⚠️ 检查项3：量能

## ⚪ 观察公司 (WATCH)
### 📌 核心结论
**⚪ 观望** | 震荡
> **一句话决策**: 等待右侧信号
| 持仓情况 | 操作建议 |
|---|---|
| 🆕 **空仓者** | 等待站回 MA20 后再评估。当前不买。 |
| 💼 **持仓者** | 维持观察。 |
"""


class DedupReportTests(unittest.TestCase):
    def _report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compact_report_20260821_us.md"
            path.write_text(SAMPLE, encoding="utf-8")
            return parse_report(path)

    def test_parses_existing_decision_fields(self):
        report = self._report()
        self.assertEqual(report.report_date, "2026-08-21")
        self.assertEqual(report.market_status, "震荡偏强")
        self.assertEqual(len(report.stocks), 2)
        item = report.stocks[0]
        self.assertEqual(item.action, "买入")
        self.assertEqual(item.symbol, "TEST")
        self.assertEqual(item.volume_ratio, "缺失")
        self.assertEqual(item.stop, "9.00 元")
        self.assertEqual(item.risk, "第一项风险")
        self.assertEqual(item.catalyst, "第一项催化")

    def test_watch_stock_is_rendered_once_in_compact_table(self):
        document = render_html(self._report())
        self.assertEqual(document.count("观察公司"), 1)
        self.assertIn("等待站回 MA20 后再评估", document)
        self.assertNotIn("检查项1", document)


if __name__ == "__main__":
    unittest.main()
