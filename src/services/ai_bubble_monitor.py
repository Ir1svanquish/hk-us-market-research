from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import yfinance as yf

from data_provider.base import DataFetcherManager
import os

from src.config import get_config
from src.services.social_sentiment_service import SocialSentimentService


# 独立监控参考池：与主程序股票列表完全解耦，不互相影响。
LEADERS = ["NVDA", "AMD", "AVGO", "TSM", "MU"]
PLATFORMS = ["MSFT", "AMZN", "META", "ORCL", "PLTR", "CRWV", "NBIS"]
HIGH_BETA = ["ASTS", "RKLB", "IREN", "TSLA"]
ETF_CODES = ["QQQ", "SMH", "SOXX", "HYG", "LQD", "VIX"]


@dataclass
class LayerSnapshot:
    name: str
    symbols: List[str]
    avg_return_5d: Optional[float] = None
    avg_return_20d: Optional[float] = None
    avg_excess_vs_qqq_20d: Optional[float] = None
    above_ma20_ratio: Optional[float] = None
    above_ma50_ratio: Optional[float] = None
    social_heat_avg: Optional[float] = None
    social_mentions_avg: Optional[float] = None
    valid_count: int = 0


@dataclass
class BubbleMonitorResult:
    as_of: str
    trend_score: int
    crowding_score: int
    breadth_score: int
    fragility_score: int
    bubble_heat: int
    break_risk: int
    status: str
    stage_label: str
    structure_label: str
    strategy_advice: str
    risk_triggers: List[str] = field(default_factory=list)
    summary: str = ''
    diagnostics: List[str] = field(default_factory=list)
    layers: Dict[str, Any] = field(default_factory=dict)
    macro: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AIBubbleMonitorService:
    def __init__(self, data_manager: Optional[DataFetcherManager] = None, as_of: Optional[str] = None):
        self.data_manager = data_manager or DataFetcherManager()
        self.as_of = self._parse_as_of(as_of)
        self.config = get_config()
        self.social_service = None
        social_api_key = getattr(self.config, 'social_sentiment_api_key', None) or os.getenv('SOCIAL_SENTIMENT_API_KEY', '')
        social_api_url = getattr(self.config, 'social_sentiment_api_url', None) or os.getenv('SOCIAL_SENTIMENT_API_URL', 'https://api.adanos.org')
        if social_api_key:
            try:
                self.social_service = SocialSentimentService(social_api_key, social_api_url)
            except Exception:
                self.social_service = None

    def run(self) -> BubbleMonitorResult:
        qqq_bench = self._get_benchmark_returns('QQQ')
        leader = self._build_layer('leaders', LEADERS, qqq_bench)
        platform = self._build_layer('platforms', PLATFORMS, qqq_bench)
        high_beta = self._build_layer('high_beta', HIGH_BETA, qqq_bench)
        macro = self._build_macro_snapshot()

        trend_score, trend_notes = self._score_trend(leader, macro)
        crowding_score, crowding_notes = self._score_crowding(leader, platform, high_beta)
        breadth_score, breadth_notes = self._score_breadth(leader, platform, high_beta)
        fragility_score, fragility_notes = self._score_fragility(leader, platform, high_beta, macro)

        bubble_heat = round(trend_score * 0.30 + crowding_score * 0.35 + breadth_score * 0.35)
        break_risk = round(crowding_score * 0.25 + breadth_score * 0.25 + fragility_score * 0.50)
        status = self._map_status(trend_score, crowding_score, breadth_score, fragility_score)
        stage_label, structure_label = self._explain_stage(status, leader, platform, high_beta, macro)
        strategy_advice, risk_triggers = self._build_action_plan(status, stage_label, structure_label, leader, platform, high_beta, macro)
        summary = self._build_summary(status, stage_label, structure_label, strategy_advice, bubble_heat, break_risk, leader, platform, high_beta, macro)

        diagnostics = trend_notes + crowding_notes + breadth_notes + fragility_notes
        return BubbleMonitorResult(
            as_of=macro.get('as_of', ''),
            trend_score=trend_score,
            crowding_score=crowding_score,
            breadth_score=breadth_score,
            fragility_score=fragility_score,
            bubble_heat=bubble_heat,
            break_risk=break_risk,
            status=status,
            stage_label=stage_label,
            structure_label=structure_label,
            strategy_advice=strategy_advice,
            risk_triggers=risk_triggers,
            summary=summary,
            diagnostics=diagnostics,
            layers={
                'leaders': asdict(leader),
                'platforms': asdict(platform),
                'high_beta': asdict(high_beta),
            },
            macro=macro,
        )

    def _get_benchmark_returns(self, symbol: str) -> Dict[str, Optional[float]]:
        try:
            df, _ = self.data_manager.get_daily_data(symbol, days=120)
            return self._calc_returns(df)
        except Exception:
            return {'ret_5d': None, 'ret_20d': None}

    def _build_layer(self, name: str, symbols: List[str], qqq_bench: Dict[str, Optional[float]]) -> LayerSnapshot:
        ret_5d = []
        ret_20d = []
        excess_20d = []
        ma20_hits = 0
        ma50_hits = 0
        valid = 0
        social_heats = []
        social_mentions = []

        for symbol in symbols:
            try:
                df, _ = self.data_manager.get_daily_data(symbol, days=120)
                metrics = self._calc_returns(df)
                if metrics['ret_5d'] is None:
                    continue
                valid += 1
                ret_5d.append(metrics['ret_5d'])
                if metrics['ret_20d'] is not None:
                    ret_20d.append(metrics['ret_20d'])
                    if qqq_bench.get('ret_20d') is not None:
                        excess_20d.append(metrics['ret_20d'] - qqq_bench['ret_20d'])
                if metrics['above_ma20']:
                    ma20_hits += 1
                if metrics['above_ma50']:
                    ma50_hits += 1

                social = self._get_social_snapshot(symbol)
                if social.get('heat_avg') is not None:
                    social_heats.append(social['heat_avg'])
                if social.get('mentions_avg') is not None:
                    social_mentions.append(social['mentions_avg'])
            except Exception:
                continue

        return LayerSnapshot(
            name=name,
            symbols=symbols,
            avg_return_5d=self._avg(ret_5d),
            avg_return_20d=self._avg(ret_20d),
            avg_excess_vs_qqq_20d=self._avg(excess_20d),
            above_ma20_ratio=(ma20_hits / valid) if valid else None,
            above_ma50_ratio=(ma50_hits / valid) if valid else None,
            social_heat_avg=self._avg(social_heats),
            social_mentions_avg=self._avg(social_mentions),
            valid_count=valid,
        )

    def _get_social_snapshot(self, symbol: str) -> Dict[str, Optional[float]]:
        if not self.social_service:
            return {'heat_avg': None, 'mentions_avg': None}
        try:
            data = self.social_service.get_social_data(symbol)
            if not data:
                return {'heat_avg': None, 'mentions_avg': None}
            values = []
            mentions = []
            for key in ('reddit', 'x', 'polymarket'):
                item = data.get(key) or {}
                heat = item.get('heat_score') or item.get('buzz_score') or item.get('heat') or item.get('buzz')
                mention = item.get('total_mentions') or item.get('mentions') or item.get('mention_count') or item.get('trade_count') or item.get('trades')
                if isinstance(heat, (int, float)):
                    values.append(float(heat))
                if isinstance(mention, (int, float)):
                    mentions.append(float(mention))
            return {
                'heat_avg': self._avg(values),
                'mentions_avg': self._avg(mentions),
            }
        except Exception:
            return {'heat_avg': None, 'mentions_avg': None}

    def _build_macro_snapshot(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {'as_of': self.as_of.isoformat() if self.as_of else ''}
        for code in ETF_CODES:
            snap[code] = self._get_historical_proxy_snapshot(code)
        try:
            hist = yf.Ticker('^TNX').history(period='6mo')
            if hist is not None and not hist.empty:
                hist = self._slice_history_to_as_of(hist)
            snap['TNX'] = float(hist['Close'].iloc[-1]) if hist is not None and not hist.empty else None
        except Exception:
            snap['TNX'] = None
        return snap

    def _score_trend(self, leader: LayerSnapshot, macro: Dict[str, Any]):
        score = 0
        notes = []
        if self._ge(leader.avg_return_5d, 4):
            score += 30; notes.append('龙头5日涨幅偏强')
        elif self._ge(leader.avg_return_5d, 2):
            score += 20; notes.append('龙头5日涨幅中等偏强')
        elif self._ge(leader.avg_return_5d, 0):
            score += 10
        if self._ge(leader.avg_excess_vs_qqq_20d, 5):
            score += 25; notes.append('龙头20日显著跑赢QQQ')
        elif self._ge(leader.avg_excess_vs_qqq_20d, 2):
            score += 15
        if self._ge(leader.above_ma20_ratio, 0.7):
            score += 15
        if self._ge(leader.above_ma50_ratio, 0.6):
            score += 10
        smh = (macro.get('SMH') or {}).get('change_pct')
        qqq = (macro.get('QQQ') or {}).get('change_pct')
        if isinstance(smh, (int, float)) and isinstance(qqq, (int, float)) and smh > qqq:
            score += 10; notes.append('半导体强于泛科技')
        return min(score, 100), notes

    def _score_crowding(self, leader: LayerSnapshot, platform: LayerSnapshot, high_beta: LayerSnapshot):
        score = 0
        notes = []
        if self._ge(leader.social_heat_avg, 75):
            score += 30; notes.append('龙头社交热度较高')
        elif self._ge(leader.social_heat_avg, 60):
            score += 20; notes.append('龙头社交热度中高')
        if self._ge(platform.social_heat_avg, 60):
            score += 20; notes.append('应用层热度开始升温')
        elif self._ge(platform.social_heat_avg, 45):
            score += 10
        if self._ge(high_beta.social_heat_avg, 50):
            score += 20; notes.append('高弹性层热度扩散')
        elif self._ge(high_beta.social_heat_avg, 35):
            score += 10
        if self._relative_ge(high_beta.social_mentions_avg, leader.social_mentions_avg, 0.60):
            score += 15; notes.append('尾部票讨论度明显抬升')
        elif self._relative_ge(platform.social_mentions_avg, leader.social_mentions_avg, 0.75):
            score += 10; notes.append('应用层讨论度接近龙头')
        if self._ge(platform.avg_return_5d, leader.avg_return_5d):
            score += 10
        if self._ge(high_beta.avg_return_5d, platform.avg_return_5d) and self._ge(high_beta.social_heat_avg, platform.social_heat_avg):
            score += 10; notes.append('价格与热度同时向高弹性层扩散')
        return min(score, 100), notes

    def _score_breadth(self, leader: LayerSnapshot, platform: LayerSnapshot, high_beta: LayerSnapshot):
        score = 0
        notes = []
        if self._ge(platform.avg_return_5d, 1.5):
            score += 20
        if self._ge(high_beta.avg_return_5d, 2.0):
            score += 25; notes.append('高弹性层启动')
        if self._ge(platform.above_ma20_ratio, 0.6):
            score += 20
        if self._ge(high_beta.above_ma20_ratio, 0.5):
            score += 15
        if self._ge(high_beta.avg_return_5d, leader.avg_return_5d):
            score += 20; notes.append('尾部涨速不弱于龙头')
        return min(score, 100), notes

    def _score_fragility(self, leader: LayerSnapshot, platform: LayerSnapshot, high_beta: LayerSnapshot, macro: Dict[str, Any]):
        score = 0
        notes = []
        vix = (macro.get('VIX') or {}).get('price')
        tnx = macro.get('TNX')
        hyg = (macro.get('HYG') or {}).get('change_pct')
        lqd = (macro.get('LQD') or {}).get('change_pct')
        qqq = (macro.get('QQQ') or {}).get('change_pct')
        if self._ge(vix, 20):
            score += 25; notes.append('VIX 抬升')
        elif self._ge(vix, 17):
            score += 15
        if self._ge(tnx, 4.65):
            score += 25; notes.append('10Y 利率偏高')
        elif self._ge(tnx, 4.45):
            score += 15
        if isinstance(hyg, (int, float)) and isinstance(lqd, (int, float)) and hyg < lqd:
            score += 15; notes.append('信用偏好转弱')
        if self._ge(high_beta.avg_return_5d, leader.avg_return_5d) and self._ge(platform.avg_return_5d, leader.avg_return_5d):
            score += 20; notes.append('尾部与应用层开始接棒')
        if isinstance(qqq, (int, float)) and qqq < 0 and self._ge(high_beta.avg_return_5d, 2.0):
            score += 15; notes.append('指数转弱但高弹性票仍躁动')
        return min(score, 100), notes

    def _map_status(self, trend: int, crowding: int, breadth: int, fragility: int) -> str:
        if fragility >= 65:
            return '退潮预警'
        if crowding >= 70 and breadth >= 70 and fragility >= 45:
            return '尾部泡沫化'
        if crowding >= 65 and breadth >= 60:
            return '全面扩散'
        if trend >= 60 and crowding >= 50:
            return '拥挤升温'
        if trend >= 55:
            return '健康主升'
        return '观察期'

    def _build_summary(self, status: str, stage_label: str, structure_label: str, strategy_advice: str, bubble_heat: int, break_risk: int, leader: LayerSnapshot, platform: LayerSnapshot, high_beta: LayerSnapshot, macro: Dict[str, Any]) -> str:
        return (
            f"状态={status}；阶段={stage_label}；结构={structure_label}；策略={strategy_advice}；"
            f"泡沫热度 {bubble_heat}/100；破裂风险 {break_risk}/100；"
            f"龙头5日均涨幅 {self._fmt(leader.avg_return_5d)}%，"
            f"应用层 {self._fmt(platform.avg_return_5d)}%，"
            f"高弹性层 {self._fmt(high_beta.avg_return_5d)}%；"
            f"VIX {self._fmt((macro.get('VIX') or {}).get('price'))}，10Y {self._fmt(macro.get('TNX'))}%"
        )

    def _build_action_plan(self, status: str, stage_label: str, structure_label: str, leader: LayerSnapshot, platform: LayerSnapshot, high_beta: LayerSnapshot, macro: Dict[str, Any]) -> tuple[str, List[str]]:
        vix = (macro.get('VIX') or {}).get('price')
        tnx = macro.get('TNX')
        qqq = (macro.get('QQQ') or {}).get('price')
        smh = (macro.get('SMH') or {}).get('price')
        leader_ret = leader.avg_return_5d or 0.0
        high_beta_ret = high_beta.avg_return_5d or 0.0

        if status == '退潮预警':
            advice = '降低高弹性敞口，优先回到龙头或现金，等待波动收敛。'
        elif status == '尾部泡沫化':
            advice = '停止追逐尾部加速票，保留最强龙头，分批兑现高波动收益。'
        elif status == '全面扩散':
            if high_beta_ret > leader_ret:
                advice = '留强去弱，控制尾部追高，优先持有仍有兑现支撑的核心龙头。'
            else:
                advice = '可继续顺主线持有，但避免在扩散末端追高。'
        elif status == '拥挤升温':
            advice = '以龙头和平台核心仓位为主，回调低吸优于盘中追涨。'
        elif status == '健康主升':
            advice = '顺着主线持有龙头，优先观察回调承接，不急于切向尾部。'
        else:
            advice = '等待趋势和情绪进一步确认，先不扩大风险暴露。'

        triggers: List[str] = []
        if isinstance(qqq, (int, float)):
            triggers.append(f'若 QQQ 跌破 {qqq * 0.985:.1f}，说明科技风险偏好开始转弱')
        if isinstance(smh, (int, float)):
            triggers.append(f'若 SMH 跌破 {smh * 0.985:.1f}，说明半导体主线强度下降')
        if isinstance(vix, (int, float)):
            triggers.append(f'若 VIX 升至 {max(18.0, vix + 1.5):.1f} 上方，说明波动重新定价')
        if isinstance(tnx, (int, float)):
            triggers.append(f'若 10Y 升至 {max(4.65, tnx + 0.10):.2f}% 上方，高估值压力会明显抬升')
        if high_beta_ret > leader_ret:
            triggers.append('若高弹性层继续跑赢龙头且龙头涨幅钝化，视为尾部泡沫进一步强化')

        return advice, triggers[:5]

    def _explain_stage(self, status: str, leader: LayerSnapshot, platform: LayerSnapshot, high_beta: LayerSnapshot, macro: Dict[str, Any]) -> tuple[str, str]:
        leader_ret = leader.avg_return_5d or 0.0
        platform_ret = platform.avg_return_5d or 0.0
        high_beta_ret = high_beta.avg_return_5d or 0.0
        vix = (macro.get('VIX') or {}).get('price') or 0.0

        if status == '退潮预警':
            return '高位脆弱期', '风险偏好回落，主线承压'
        if status == '尾部泡沫化':
            return '尾部泡沫化', '高弹性票主导，龙头边际钝化'
        if status == '全面扩散':
            if high_beta_ret > leader_ret and high_beta_ret > max(platform_ret, 0):
                return '情绪外溢期', '高弹性层领跑，主线从龙头向尾部扩散'
            return '扩散深化期', '龙头之外的应用层与情绪层同步活跃'
        if status == '拥挤升温':
            if platform_ret >= 0:
                return '龙头扩散期', '龙头仍稳，资金开始向平台层外溢'
            return '龙头拥挤期', '核心资产维持强势，但扩散尚不充分'
        if status == '健康主升':
            if vix and vix < 17:
                return '健康主升期', '龙头驱动为主，外部波动尚低'
            return '强趋势推进期', '龙头主线延续，但需留意外部扰动'
        return '观察期', '结构信号尚未统一，等待进一步确认'

    def _calc_returns(self, df):
        if df is None or df.empty:
            return {'ret_5d': None, 'ret_20d': None, 'above_ma20': False, 'above_ma50': False}
        close_col = 'close' if 'close' in df.columns else 'Close'
        series = df[close_col]
        if self.as_of is not None:
            try:
                if hasattr(df.index, 'date'):
                    series = series[df.index.date <= self.as_of]
            except Exception:
                pass
            for date_col in ('date', 'datetime', 'Date'):
                if date_col in df.columns:
                    try:
                        mask = df[date_col].astype(str) <= self.as_of.isoformat()
                        series = df.loc[mask, close_col]
                    except Exception:
                        pass
                    break
        closes = series.dropna()
        if closes.empty:
            return {'ret_5d': None, 'ret_20d': None, 'above_ma20': False, 'above_ma50': False}
        latest = float(closes.iloc[-1])
        ret_5d = ((latest / float(closes.iloc[-6])) - 1) * 100 if len(closes) >= 6 else None
        ret_20d = ((latest / float(closes.iloc[-21])) - 1) * 100 if len(closes) >= 21 else None
        ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else None
        ma50 = float(closes.tail(50).mean()) if len(closes) >= 50 else None
        return {
            'ret_5d': ret_5d,
            'ret_20d': ret_20d,
            'above_ma20': bool(ma20 is not None and latest >= ma20),
            'above_ma50': bool(ma50 is not None and latest >= ma50),
        }

    def _get_historical_proxy_snapshot(self, code: str) -> Dict[str, Optional[float]]:
        try:
            df, _ = self.data_manager.get_daily_data(code, days=120)
            if df is None or df.empty:
                return {'price': None, 'change_pct': None}
            if self.as_of is not None:
                for date_col in ('date', 'datetime', 'Date'):
                    if date_col in df.columns:
                        try:
                            df = df[df[date_col].astype(str) <= self.as_of.isoformat()]
                        except Exception:
                            pass
                        break
            if df.empty:
                return {'price': None, 'change_pct': None}
            close_col = 'close' if 'close' in df.columns else 'Close'
            latest = float(df[close_col].iloc[-1])
            prev = float(df[close_col].iloc[-2]) if len(df) >= 2 else latest
            pct = ((latest / prev) - 1) * 100 if prev else None
            return {'price': latest, 'change_pct': pct}
        except Exception:
            return {'price': None, 'change_pct': None}

    def _slice_history_to_as_of(self, hist):
        if self.as_of is None or hist is None or hist.empty:
            return hist
        try:
            return hist[hist.index.date <= self.as_of]
        except Exception:
            return hist

    @staticmethod
    def _parse_as_of(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except Exception:
            return None

    @staticmethod
    def _avg(values: List[float]) -> Optional[float]:
        vals = [float(v) for v in values if isinstance(v, (int, float))]
        return sum(vals) / len(vals) if vals else None

    @staticmethod
    def _ge(value: Optional[float], threshold: Optional[float]) -> bool:
        return isinstance(value, (int, float)) and isinstance(threshold, (int, float)) and value >= threshold

    @staticmethod
    def _relative_ge(value: Optional[float], base: Optional[float], ratio: float) -> bool:
        return (
            isinstance(value, (int, float))
            and isinstance(base, (int, float))
            and base > 0
            and value >= base * ratio
        )

    @staticmethod
    def _fmt(value: Optional[float]) -> str:
        return f'{value:.2f}' if isinstance(value, (int, float)) else 'N/A'
